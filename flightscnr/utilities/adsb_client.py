"""Live aircraft positions from adsb.fi (free, no API key — same source as FlightScnr)."""

import logging
import time

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://opendata.adsb.fi/api/v3/lat/"
_CACHE = {"entries": [], "ts": 0.0}
_CACHE_TTL_S = 2


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


def _to_entry(plane: dict, min_altitude: int) -> dict | None:
    lat = plane.get("lat")
    lon = plane.get("lon")
    if not _valid_position(lat, lon):
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
    plane_type = plane.get("t") or ""
    airline = (plane.get("ownOp") or "").strip()
    if airline and airline == airline.upper():
        airline = airline.title()

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
        vert = int(round(float(plane.get("baro_rate") or 0)))
    except (TypeError, ValueError):
        vert = 0

    return {
        "callsign": callsign,
        "airline": airline,
        "plane": plane_type,
        "origin": "",
        "destination": "",
        "plane_latitude": float(lat),
        "plane_longitude": float(lon),
        "altitude": alt_ft,
        "ground_speed": gs,
        "heading": heading,
        "vertical_speed": vert,
        "data_source": "adsb_fi",
    }


def fetch_aircraft_entries(
    lat: float,
    lon: float,
    radius_nm: float,
    min_altitude: int = 0,
) -> list[dict]:
    """Return flight dicts compatible with overhead/radar display."""
    global _CACHE
    now = time.time()
    if now - _CACHE["ts"] < _CACHE_TTL_S and _CACHE["entries"]:
        return _CACHE["entries"]

    url = f"{API_BASE}{lat:.6f}/lon/{lon:.6f}/dist/{radius_nm:.1f}"
    try:
        resp = requests.get(url, timeout=(5, 15))
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("adsb.fi fetch failed: %s", exc)
        return _CACHE["entries"]
    except ValueError as exc:
        logger.warning("adsb.fi invalid JSON: %s", exc)
        return _CACHE["entries"]

    entries = []
    for plane in data.get("ac") or []:
        entry = _to_entry(plane, min_altitude)
        if entry:
            entries.append(entry)

    _CACHE["entries"] = entries
    _CACHE["ts"] = now
    logger.info("adsb.fi: %d aircraft within %.1fnm of %.4f,%.4f", len(entries), radius_nm, lat, lon)
    return entries
