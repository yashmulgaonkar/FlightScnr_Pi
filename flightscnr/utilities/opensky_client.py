"""OpenSky Network — route enrichment fallback (origin/destination only).

Used when AirLabs and FlightAware both lack a route. OpenSky derives
origin/destination from historical track data rather than a filed flight
plan, so results can be delayed or missing while a flight is still en
route. Requires an OpenSky API client (client_id/client_secret), separate
from the OPENSKY_USERNAME/OPENSKY_SERIAL used for feeding.
"""

from __future__ import annotations

import logging
import threading
from time import time

import requests

logger = logging.getLogger(__name__)

_TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network"
    "/protocol/openid-connect/token"
)
_API_BASE = "https://opensky-network.org/api"
_LOOKBACK_S = 6 * 3600  # how far back to search for the current flight leg

_CACHE_TTL_S = 900  # 15 minutes — routes rarely change mid-flight
_cache: dict[str, tuple[dict | None, float]] = {}
_CACHE_MAX = 128
_lock = threading.Lock()

_token_lock = threading.Lock()
_token: str | None = None
_token_expiry: float = 0.0


def _cache_put(icao24: str, value: dict | None) -> None:
    now = time()
    if len(_cache) >= _CACHE_MAX:
        for k in [k for k, (_, ts) in list(_cache.items()) if now - ts >= _CACHE_TTL_S]:
            _cache.pop(k, None)
        while len(_cache) >= _CACHE_MAX:
            _cache.pop(min(_cache, key=lambda k: _cache[k][1]), None)
    _cache[icao24] = (value, now)


def _cache_get(icao24: str) -> dict | None:
    entry = _cache.get(icao24)
    if not entry:
        return None
    value, ts = entry
    if time() - ts >= _CACHE_TTL_S:
        return None
    return value


def _credentials() -> tuple[str, str]:
    try:
        from config import OPENSKY_API_CLIENT_ID, OPENSKY_API_CLIENT_SECRET

        cid = (OPENSKY_API_CLIENT_ID or "").strip()
        secret = (OPENSKY_API_CLIENT_SECRET or "").strip()
    except Exception:
        cid = secret = ""
    if not cid or not secret:
        import os

        cid = cid or (os.environ.get("OPENSKY_API_CLIENT_ID") or "").strip()
        secret = secret or (os.environ.get("OPENSKY_API_CLIENT_SECRET") or "").strip()
    return cid, secret


def _get_token() -> str | None:
    """Return a cached bearer token, refreshing ~1 minute before expiry."""
    global _token, _token_expiry
    with _token_lock:
        if _token and time() < _token_expiry - 60:
            return _token

        cid, secret = _credentials()
        if not cid or not secret:
            return None

        try:
            resp = requests.post(
                _TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": cid,
                    "client_secret": secret,
                },
                timeout=(3, 8),
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.warning("OpenSky token request failed: %s", exc)
            return None
        except ValueError as exc:
            logger.warning("OpenSky token response invalid JSON: %s", exc)
            return None

        _token = data.get("access_token")
        expires_in = float(data.get("expires_in", 1800))
        _token_expiry = time() + expires_in
        return _token


def lookup_route(icao24: str) -> dict | None:
    """Return {'origin': ..., 'destination': ..., 'route_source': 'opensky'}.

    icao24 is the 24-bit ICAO hex address (lowercase, no '0x' prefix) —
    NOT the callsign. origin/destination may individually be empty when
    OpenSky hasn't derived that end yet (e.g. still airborne).
    """
    icao24 = (icao24 or "").strip().lower()
    if not icao24:
        return None

    cached = _cache_get(icao24)
    if cached is not None:
        return cached

    token = _get_token()
    if not token:
        return None

    now_s = int(time())
    try:
        resp = requests.get(
            f"{_API_BASE}/flights/aircraft",
            params={"icao24": icao24, "begin": now_s - _LOOKBACK_S, "end": now_s},
            headers={"Authorization": f"Bearer {token}"},
            timeout=(3, 8),
        )
        if resp.status_code == 404:
            _cache_put(icao24, None)
            return None
        resp.raise_for_status()
        flights = resp.json()
    except requests.RequestException as exc:
        logger.warning("OpenSky flights lookup failed (%s): %s", icao24, exc)
        return None
    except ValueError as exc:
        logger.warning("OpenSky flights response invalid JSON (%s): %s", icao24, exc)
        return None

    if not flights:
        _cache_put(icao24, None)
        return None

    # Most recent leg = best guess for the aircraft's current flight.
    latest = max(flights, key=lambda f: f.get("lastSeen") or 0)
    origin = (latest.get("estDepartureAirport") or "").strip()
    destination = (latest.get("estArrivalAirport") or "").strip()
    if not origin and not destination:
        _cache_put(icao24, None)
        return None

    result = {
        "origin": origin,
        "destination": destination,
        "route_source": "opensky",
    }
    _cache_put(icao24, result)
    return result
