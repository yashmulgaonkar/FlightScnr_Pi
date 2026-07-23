"""FlightAware AeroAPI — route / schedule enrichment only (not live radar).

Used when FR24 details and AirLabs lack origin/destination. Calls are capped
by a soft monthly USD spend ceiling so free-tier credit is not burned.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from time import time

import requests

logger = logging.getLogger(__name__)

_API_BASE = "https://aeroapi.flightaware.com/aeroapi"
_CACHE_TTL_S = 900  # 15 minutes — routes rarely change mid-flight
_cache: dict[str, tuple[dict | None, float]] = {}
_lock = threading.Lock()

DATA_DIR = os.environ.get("FLIGHTSCNR_DATA_DIR", "/var/lib/flightscnr")
USAGE_PATH = os.path.join(DATA_DIR, "flightaware_usage.json")


def _api_key() -> str:
    try:
        from config import FLIGHTAWARE_API_KEY

        key = (FLIGHTAWARE_API_KEY or "").strip()
    except Exception:
        key = ""
    if not key:
        key = (os.environ.get("FLIGHTAWARE_API_KEY") or "").strip()
    return key


def _monthly_limit() -> float:
    try:
        from config import FLIGHTAWARE_MONTHLY_LIMIT

        return float(FLIGHTAWARE_MONTHLY_LIMIT)
    except Exception:
        return float(os.environ.get("FLIGHTAWARE_MONTHLY_LIMIT", "4.50"))


def _cost_per_call() -> float:
    try:
        from config import FLIGHTAWARE_COST_PER_CALL

        return float(FLIGHTAWARE_COST_PER_CALL)
    except Exception:
        return float(os.environ.get("FLIGHTAWARE_COST_PER_CALL", "0.02"))


def _month_key(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def _load_usage() -> dict:
    try:
        with open(USAGE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return {"month": _month_key(), "spend_usd": 0.0, "calls": 0}


def _save_usage(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = USAGE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, USAGE_PATH)


def usage_status() -> dict:
    """Return current-month spend stats for portal/debug."""
    with _lock:
        data = _load_usage()
        month = _month_key()
        if data.get("month") != month:
            data = {"month": month, "spend_usd": 0.0, "calls": 0}
        limit = _monthly_limit()
        spend = float(data.get("spend_usd") or 0.0)
        return {
            "month": month,
            "spend_usd": round(spend, 4),
            "calls": int(data.get("calls") or 0),
            "monthly_limit_usd": limit,
            "remaining_usd": round(max(0.0, limit - spend), 4),
            "budget_ok": spend < limit,
        }


def _budget_allows_call() -> bool:
    status = usage_status()
    if not status["budget_ok"]:
        logger.warning(
            "FlightAware: monthly spend ceiling reached "
            "($%.2f / $%.2f) — skipping route lookup",
            status["spend_usd"],
            status["monthly_limit_usd"],
        )
        return False
    return True


def _record_call() -> None:
    with _lock:
        data = _load_usage()
        month = _month_key()
        if data.get("month") != month:
            data = {"month": month, "spend_usd": 0.0, "calls": 0}
        data["spend_usd"] = float(data.get("spend_usd") or 0.0) + _cost_per_call()
        data["calls"] = int(data.get("calls") or 0) + 1
        _save_usage(data)


def _airport_code(node) -> str:
    if not isinstance(node, dict):
        return ""
    for key in ("code_iata", "code_icao", "code", "airport_code"):
        val = (node.get(key) or "").strip().upper()
        if val:
            return val
    return ""


def _pick_flight(flights: list, ident: str) -> dict | None:
    if not flights:
        return None
    now = time()
    scored: list[tuple[float, dict]] = []
    for fl in flights:
        if not isinstance(fl, dict):
            continue
        # Prefer in-progress / recently scheduled segments.
        score = 0.0
        status = (fl.get("status") or "").lower()
        if "en route" in status or status == "airborne":
            score += 100
        elif "scheduled" in status or "filed" in status:
            score += 50
        for ts_key in ("actual_off", "estimated_off", "scheduled_off", "scheduled_out"):
            raw = fl.get(ts_key)
            if not raw:
                continue
            try:
                # ISO-ish timestamps from AeroAPI
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                score -= abs(dt.timestamp() - now) / 3600.0
            except (TypeError, ValueError):
                pass
            break
        scored.append((score, fl))
    if not scored:
        return flights[0] if isinstance(flights[0], dict) else None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def lookup_route(ident: str) -> dict | None:
    """Look up origin/destination for a callsign or registration via AeroAPI.

    Returns a dict with origin, destination, optional times/status, and
    route_source=\"flightaware\", or None when unavailable / budget blocked.
    """
    try:
        from secrets_store import api_enabled

        if not api_enabled("FLIGHTAWARE_API_KEY"):
            logger.info("FlightAware API disabled in web portal settings")
            return None
    except Exception:
        pass

    key = _api_key()
    if not key:
        return None

    ident = (ident or "").strip().upper()
    if not ident:
        return None

    cached = _cache.get(ident)
    if cached and (time() - cached[1]) < _CACHE_TTL_S:
        return cached[0]

    if not _budget_allows_call():
        _cache[ident] = (None, time())
        return None

    url = f"{_API_BASE}/flights/{ident}"
    try:
        logger.info("FlightAware: Looking up route for %s", ident)
        resp = requests.get(
            url,
            headers={"x-apikey": key, "Accept": "application/json"},
            params={"max_pages": 1},
            timeout=(5, 15),
        )
        _record_call()
        if resp.status_code == 404:
            logger.info("FlightAware: No flights for %s", ident)
            _cache[ident] = (None, time())
            return None
        resp.raise_for_status()
        data = resp.json()
        flights = data.get("flights") or []
        best = _pick_flight(flights, ident)
        if not best:
            _cache[ident] = (None, time())
            return None
        origin = _airport_code(best.get("origin"))
        destination = _airport_code(best.get("destination"))
        if not origin and not destination:
            _cache[ident] = (None, time())
            return None
        result = {
            "origin": origin,
            "destination": destination,
            "dep_time": best.get("scheduled_out")
            or best.get("scheduled_off")
            or best.get("estimated_out")
            or "",
            "arr_time": best.get("scheduled_in")
            or best.get("scheduled_on")
            or best.get("estimated_in")
            or "",
            "status": best.get("status") or "",
            "route_source": "flightaware",
        }
        logger.info(
            "FlightAware: %s %s→%s status=%s",
            ident,
            result["origin"] or "?",
            result["destination"] or "?",
            result["status"] or "?",
        )
        _cache[ident] = (result, time())
        return result
    except requests.exceptions.Timeout:
        logger.warning("FlightAware: Request timed out for %s", ident)
        _cache[ident] = (None, time())
        return None
    except Exception as exc:
        logger.warning("FlightAware: Error looking up %s: %s", ident, exc)
        _cache[ident] = (None, time())
        return None
