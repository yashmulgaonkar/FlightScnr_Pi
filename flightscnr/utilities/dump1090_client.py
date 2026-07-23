"""Local dump1090 / readsb / tar1090 aircraft.json positions.

Polls a receiver's JSON feed (default http://127.0.0.1:8080/data/aircraft.json)
and returns flight dicts compatible with overhead/radar display.
"""

from __future__ import annotations

import logging
import math
import time
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

_CACHE: dict = {"entries": [], "ts": 0.0, "url": None, "radius_nm": None}
_CACHE_TTL_S = 1.0
# Ignore tracks with no fresh position (seconds).
_MAX_SEEN_POS_S = 60.0


def _parse_alt_ft(plane: dict) -> int:
    alt = plane.get("alt_baro")
    if alt == "ground":
        return 0
    try:
        return int(float(alt))
    except (TypeError, ValueError):
        geom = plane.get("alt_geom")
        try:
            return int(float(geom))
        except (TypeError, ValueError):
            return 0


def _valid_position(lat, lon) -> bool:
    if lat is None or lon is None:
        return False
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        return False
    if abs(lat_f) < 0.01 and abs(lon_f) < 0.01:
        return False
    return -90 <= lat_f <= 90 and -180 <= lon_f <= 180


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r_nm = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r_nm * math.asin(min(1.0, math.sqrt(a)))


def _normalize_squawk(value) -> str:
    from utilities.adsb_client import normalize_squawk

    return normalize_squawk(value)


def _aircraft_json_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if not url:
        return "http://127.0.0.1:8080/data/aircraft.json"
    if url.rstrip("/").endswith("aircraft.json"):
        return url
    # Accept base like http://host:8080/ or http://host/tar1090/
    if not url.endswith("/"):
        url += "/"
    return urljoin(url, "data/aircraft.json")


def _to_entry(
    plane: dict,
    *,
    home_lat: float,
    home_lon: float,
    radius_nm: float,
    min_altitude: int,
) -> dict | None:
    lat = plane.get("lat")
    lon = plane.get("lon")
    if not _valid_position(lat, lon):
        return None

    try:
        seen = float(plane.get("seen_pos", plane.get("seen", 0)) or 0)
    except (TypeError, ValueError):
        seen = 0.0
    if seen > _MAX_SEEN_POS_S:
        return None

    lat_f = float(lat)
    lon_f = float(lon)
    if _haversine_nm(home_lat, home_lon, lat_f, lon_f) > radius_nm:
        return None

    alt_ft = _parse_alt_ft(plane)
    try:
        from config import passes_altitude_filter

        if not passes_altitude_filter(alt_ft):
            return None
    except ImportError:
        if alt_ft < min_altitude or alt_ft >= 100000:
            return None

    callsign = (plane.get("flight") or "").strip()
    plane_type = (plane.get("t") or "").strip()
    track = plane.get("track")
    if track is None:
        track = plane.get("true_heading", 0)
    try:
        gs = int(round(float(plane.get("gs") or 0)))
    except (TypeError, ValueError):
        gs = 0
    try:
        heading = int(round(float(track or 0)))
    except (TypeError, ValueError):
        heading = 0
    try:
        vert = int(round(float(plane.get("baro_rate") or plane.get("geom_rate") or 0)))
    except (TypeError, ValueError):
        vert = 0

    squawk = _normalize_squawk(plane.get("squawk"))
    icao_hex = (plane.get("hex") or "").strip().upper()
    registration = (plane.get("r") or "").strip().upper()

    return {
        "callsign": callsign,
        "icao_hex": icao_hex,
        "registration": registration,
        "airline": "",
        "plane": plane_type,
        "origin": "",
        "destination": "",
        "plane_latitude": lat_f,
        "plane_longitude": lon_f,
        "altitude": alt_ft,
        "ground_speed": gs,
        "heading": heading,
        "vertical_speed": vert,
        "squawk": squawk,
        "db_flags": 0,
        "data_source": "dump1090",
    }


def fetch_aircraft_entries(
    lat: float,
    lon: float,
    radius_nm: float,
    min_altitude: int = 0,
    *,
    url: str | None = None,
) -> list[dict]:
    """Return flight dicts from a local dump1090/readsb/tar1090 JSON feed."""
    global _CACHE
    try:
        from config import DUMP1090_URL

        configured = DUMP1090_URL
    except ImportError:
        configured = "http://127.0.0.1:8080/data/aircraft.json"
    feed_url = _aircraft_json_url(url or configured)

    now = time.time()
    if (
        now - _CACHE["ts"] < _CACHE_TTL_S
        and _CACHE["entries"] is not None
        and _CACHE["url"] == feed_url
        and _CACHE["radius_nm"] == radius_nm
    ):
        return list(_CACHE["entries"])

    try:
        resp = requests.get(feed_url, timeout=(2, 5))
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("dump1090 fetch failed (%s): %s", feed_url, exc)
        return list(_CACHE["entries"] or [])
    except ValueError as exc:
        logger.warning("dump1090 invalid JSON (%s): %s", feed_url, exc)
        return list(_CACHE["entries"] or [])

    aircraft = data.get("aircraft") or data.get("ac") or []
    entries: list[dict] = []
    for plane in aircraft:
        if not isinstance(plane, dict):
            continue
        entry = _to_entry(
            plane,
            home_lat=lat,
            home_lon=lon,
            radius_nm=radius_nm,
            min_altitude=min_altitude,
        )
        if entry:
            entries.append(entry)

    _CACHE["entries"] = entries
    _CACHE["ts"] = now
    _CACHE["url"] = feed_url
    _CACHE["radius_nm"] = radius_nm
    logger.info(
        "dump1090: %d aircraft within %.1fnm of %.4f,%.4f (%s)",
        len(entries),
        radius_nm,
        lat,
        lon,
        feed_url,
    )
    return list(entries)
