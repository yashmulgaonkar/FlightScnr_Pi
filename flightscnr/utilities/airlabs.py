"""
airlabs.py — Flight schedule lookup via AirLabs API.

Used to get departure/arrival info for flights that haven't taken off yet.
The FR24 gRPC feed only shows airborne flights — AirLabs fills the gap
for pre-departure schedule data.

Free tier: 1000 credits/month, 1 credit per /schedules call.
API key set via AIRLABS_API_KEY env var or config.

Usage:
    from utilities.airlabs import get_flight_schedule
    sched = get_flight_schedule("UA353")
    # {"origin": "EWR", "destination": "LAX", "dep_time": "2026-05-11 18:30", ...}
"""

import logging
import os
from time import time

import requests

logger = logging.getLogger(__name__)

_API_BASE = "https://airlabs.co/api/v9"

# Module-level cache: callsign -> (result, timestamp)
# Prevents repeated API calls for the same flight (web UI + overhead.py)
_cache = {}
_CACHE_TTL = 300  # 5 minutes

# Try config first, fall back to env var
try:
    from config import AIRLABS_API_KEY
except (ImportError, ModuleNotFoundError, NameError):
    AIRLABS_API_KEY = None

if not AIRLABS_API_KEY:
    AIRLABS_API_KEY = os.environ.get("AIRLABS_API_KEY", "")


def get_flight_schedule(callsign):
    """
    Look up flight schedule from AirLabs.

    Accepts IATA (UA353) or ICAO (UAL353) format.
    Returns the next upcoming segment for this flight number, or None.

    Returns:
        {
            "origin": "EWR",
            "destination": "LAX",
            "dep_time": "2026-05-11 18:30",
            "dep_time_utc": "2026-05-11 22:30",
            "arr_time": "2026-05-11 21:45",
            "arr_time_utc": "2026-05-12 01:45",
            "status": "scheduled",
            "airline_iata": "UA",
            "flight_number": "UA353",
            "duration": 330,
        }
        or None on error / not found.
    """
    if not AIRLABS_API_KEY:
        logger.warning("AirLabs: No API key configured")
        return None

    callsign = callsign.strip().upper()
    if not callsign:
        return None

    # Check module-level cache first
    cached = _cache.get(callsign)
    if cached and (time() - cached[1]) < _CACHE_TTL:
        return cached[0]

    # Determine if IATA (2-letter + digits) or ICAO (3-letter + digits)
    params = {"api_key": AIRLABS_API_KEY}
    if len(callsign) >= 4 and callsign[:3].isalpha() and callsign[3:].isdigit():
        params["flight_icao"] = callsign
    else:
        params["flight_iata"] = callsign

    try:
        logger.info(f"AirLabs: Looking up schedule for {callsign}")
        r = requests.get(f"{_API_BASE}/schedules", params=params, timeout=(5, 15))
        r.raise_for_status()
        data = r.json()

        schedules = data.get("response", [])
        if not schedules:
            logger.info(f"AirLabs: No schedule found for {callsign}")
            _cache[callsign] = (None, time())
            return None

        # Pick the next upcoming segment (smallest dep_time_ts in the future)
        now = time()
        upcoming = [
            s for s in schedules
            if s.get("dep_time_ts") and s["dep_time_ts"] > now - 3600  # within last hour or future
        ]

        if not upcoming:
            # Fall back to first result if nothing upcoming
            upcoming = schedules

        # Sort by departure time, pick earliest future one
        upcoming.sort(key=lambda s: s.get("dep_time_ts", 0))
        best = upcoming[0]

        result = {
            "origin": best.get("dep_iata", ""),
            "destination": best.get("arr_iata", ""),
            "dep_time": best.get("dep_time", ""),
            "dep_time_utc": best.get("dep_time_utc", ""),
            "arr_time": best.get("arr_time", ""),
            "arr_time_utc": best.get("arr_time_utc", ""),
            "status": best.get("status", ""),
            "airline_iata": best.get("airline_iata", ""),
            "flight_number": best.get("flight_iata", callsign),
            "duration": best.get("duration"),
        }
        logger.info(f"AirLabs: Found {result['flight_number']} {result['origin']}→{result['destination']} status={result['status']}")
        _cache[callsign] = (result, time())
        return result

    except requests.exceptions.Timeout:
        logger.warning("AirLabs: Request timed out")
        _cache[callsign] = (None, time())
        return None
    except Exception as e:
        logger.warning(f"AirLabs: Error looking up {callsign}: {e}")
        _cache[callsign] = (None, time())
        return None
