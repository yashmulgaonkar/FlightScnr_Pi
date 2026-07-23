"""
Configuration — all values sourced exclusively from environment variables.

NO user-configurable defaults are stored in this file.
All configuration must be provided via:
  - /etc/flightscnr.env (systemd EnvironmentFile for production)
  - .env file in the project root (for local development via python-dotenv)
  - location.json in FLIGHTSCNR_DATA_DIR (radar center set via web portal)

See .env.example for documentation of all available variables and their defaults.
"""
import json
import logging
import math
import os

logger = logging.getLogger(__name__)

# Load .env file if present (for local dev; systemd uses EnvironmentFile instead)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
except ImportError:
    pass

# User-friendly keys: config.h + web portal secrets.json (env vars still win)
try:
    from secrets_store import bootstrap_secrets
    bootstrap_secrets()
except ImportError:
    pass


def _bool(val: str) -> bool:
    return val.strip().lower() in ("true", "1", "yes", "on")


def _require(name: str) -> str:
    """Return env var value or empty string (caller decides how to handle missing)."""
    return os.environ.get(name, "")


def _float_env(name: str):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return float(raw)


def _zone_from_home(lat: float, lon: float, radius_nm: float) -> dict:
    """Build a square bounding box from home coordinates and radius in nautical miles."""
    lat_delta = radius_nm / 60.0
    lon_delta = radius_nm / (60.0 * max(0.01, math.cos(math.radians(lat))))
    return {
        "tl_y": lat + lat_delta,
        "tl_x": lon - lon_delta,
        "br_y": lat - lat_delta,
        "br_x": lon + lon_delta,
    }


def zone_from_radius_nm(radius_nm: float, home: list | None = None) -> dict:
    """Square bounding box centered on home (defaults to LOCATION_HOME)."""
    if home is None:
        home = LOCATION_HOME
    return _zone_from_home(home[0], home[1], radius_nm)


def _resolve_location():
    """Resolve home point and search zone from env vars."""
    zone = {
        "tl_y": _float_env("ZONE_TL_LAT"),
        "tl_x": _float_env("ZONE_TL_LON"),
        "br_y": _float_env("ZONE_BR_LAT"),
        "br_x": _float_env("ZONE_BR_LON"),
    }
    home_lat = _float_env("HOME_LAT")
    home_lon = _float_env("HOME_LON")
    radius_nm = float(os.environ.get("SEARCH_RADIUS_NM", "15"))

    if all(v is not None for v in zone.values()):
        if home_lat is None or home_lon is None:
            home_lat = (zone["tl_y"] + zone["br_y"]) / 2
            home_lon = (zone["tl_x"] + zone["br_x"]) / 2
        return [home_lat, home_lon], zone, "zone_corners"

    if home_lat is not None and home_lon is not None:
        return [home_lat, home_lon], _zone_from_home(home_lat, home_lon, radius_nm), "home_radius"

    return [0.0, 0.0], {"tl_y": 0.0, "tl_x": 0.0, "br_y": 0.0, "br_x": 0.0}, "unset"


# --- API Keys ---
FR24_API_KEY = _require("FR24_API_KEY")
TOMORROW_API_KEY = _require("TOMORROW_API_KEY")
AIRLABS_API_KEY = os.environ.get("AIRLABS_API_KEY", "")
AISSTREAM_API_KEY = os.environ.get("AISSTREAM_API_KEY", "")

# --- Location (zone + home) ---
LOCATION_HOME, ZONE_HOME, LOCATION_SOURCE = _resolve_location()
SEARCH_RADIUS_NM = float(os.environ.get("SEARCH_RADIUS_NM", "15"))
ADSB_ENABLED = _bool(os.environ.get("ADSB_ENABLED", "True"))
# Local dump1090-fa / readsb / tar1090 JSON feed (empty URL disables even if ENABLED).
DUMP1090_ENABLED = _bool(os.environ.get("DUMP1090_ENABLED", "False"))
DUMP1090_URL = os.environ.get(
    "DUMP1090_URL",
    "http://127.0.0.1:8080/data/aircraft.json",
).strip()
FLIGHTAWARE_API_KEY = os.environ.get("FLIGHTAWARE_API_KEY", "")
# Soft monthly spend ceiling in USD (AeroAPI free credit is typically ~$5).
FLIGHTAWARE_MONTHLY_LIMIT = float(os.environ.get("FLIGHTAWARE_MONTHLY_LIMIT", "4.50"))
# Conservative per-call cost estimate for /flights/{ident} enrichment.
FLIGHTAWARE_COST_PER_CALL = float(os.environ.get("FLIGHTAWARE_COST_PER_CALL", "0.02"))
# NASA FIRMS free MAP_KEY for wildfire detections on the radar.
# https://firms.modaps.eosdis.nasa.gov/api/map_key/
FIRMS_MAP_KEY = os.environ.get("FIRMS_MAP_KEY", "")
DATA_REFRESH_SECONDS = float(os.environ.get("DATA_REFRESH_SECONDS", "2"))
# How often to merge AIS vessels into the radar (WebSocket still pushes continuously)
AIS_REFRESH_SECONDS = float(os.environ.get("AIS_REFRESH_SECONDS", "5"))
LOCATION_FILE = os.path.join(
    os.environ.get("FLIGHTSCNR_DATA_DIR", "/var/lib/flightscnr"),
    "location.json",
)
_location_file_mtime: float | None = None


def parse_lat_lon_pair(text: str) -> tuple[float, float]:
    """Parse 'lat, lon' (e.g. '37.639799, -122.368049')."""
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("Enter coordinates as latitude, longitude")
    lat = float(parts[0])
    lon = float(parts[1])
    if not -90.0 <= lat <= 90.0:
        raise ValueError("Latitude must be between -90 and 90")
    if not -180.0 <= lon <= 180.0:
        raise ValueError("Longitude must be between -180 and 180")
    return lat, lon


def format_location_home() -> str:
    return f"{LOCATION_HOME[0]:.6f}, {LOCATION_HOME[1]:.6f}"


def _apply_home(lat: float, lon: float, source: str | None = None) -> bool:
    """Apply home coordinates. Returns True if lat/lon actually changed."""
    global LOCATION_SOURCE, TEMPERATURE_LOCATION
    old_lat, old_lon = float(LOCATION_HOME[0]), float(LOCATION_HOME[1])
    changed = abs(old_lat - lat) > 1e-7 or abs(old_lon - lon) > 1e-7
    LOCATION_HOME[0] = lat
    LOCATION_HOME[1] = lon
    ZONE_HOME.clear()
    ZONE_HOME.update(_zone_from_home(lat, lon, SEARCH_RADIUS_NM))
    if source is not None:
        LOCATION_SOURCE = source
    elif LOCATION_SOURCE == "unset":
        LOCATION_SOURCE = "home_radius"
    # Weather always follows the radar center when home moves. An env-only
    # TEMPERATURE_LOCATION used to freeze weather at the boot-time coordinates.
    TEMPERATURE_LOCATION = f"{lat},{lon}"
    return changed


def _save_location_file(lat: float, lon: float):
    os.makedirs(os.path.dirname(LOCATION_FILE), exist_ok=True)
    payload = {"lat": lat, "lon": lon}
    tmp_path = LOCATION_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp_path, LOCATION_FILE)
    try:
        os.chmod(LOCATION_FILE, 0o666)
    except OSError:
        pass


def set_location_home(lat: float, lon: float):
    """Persist and apply radar center coordinates."""
    global _location_file_mtime
    _save_location_file(lat, lon)
    _apply_home(lat, lon, "portal")
    try:
        _location_file_mtime = os.path.getmtime(LOCATION_FILE)
    except OSError:
        _location_file_mtime = None


def reload_location_override() -> bool:
    """Reload location.json when changed by another process (e.g. web portal)."""
    global _location_file_mtime
    try:
        mtime = os.path.getmtime(LOCATION_FILE) if os.path.isfile(LOCATION_FILE) else None
    except OSError:
        mtime = None
    if mtime is not None and mtime == _location_file_mtime:
        return False
    if not os.path.isfile(LOCATION_FILE):
        _location_file_mtime = mtime
        return False
    try:
        with open(LOCATION_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        lat = float(data["lat"])
        lon = float(data["lon"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("Could not load saved location from %s: %s", LOCATION_FILE, exc)
        _location_file_mtime = mtime
        return False
    changed = _apply_home(lat, lon, "portal")
    _location_file_mtime = mtime
    return changed


def _bootstrap_location_override():
    global _location_file_mtime
    if not os.path.isfile(LOCATION_FILE):
        return
    try:
        _location_file_mtime = os.path.getmtime(LOCATION_FILE)
        with open(LOCATION_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        _apply_home(float(data["lat"]), float(data["lon"]), "portal")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("Could not apply saved location from %s: %s", LOCATION_FILE, exc)


_bootstrap_location_override()


def location_configured() -> bool:
    return LOCATION_SOURCE != "unset"


def location_status() -> str:
    if not location_configured():
        return "Location not set — use web portal or /etc/flightscnr.env"
    if LOCATION_SOURCE == "portal":
        return f"Radar center set via web ({SEARCH_RADIUS_NM:g}nm search)"
    if LOCATION_SOURCE == "home_radius":
        return f"Searching {SEARCH_RADIUS_NM:g}nm around home"
    return "Searching configured zone"

# --- Weather ---
TEMPERATURE_LOCATION = _require("TEMPERATURE_LOCATION")
if not TEMPERATURE_LOCATION and location_configured():
    TEMPERATURE_LOCATION = f"{LOCATION_HOME[0]},{LOCATION_HOME[1]}"
TEMPERATURE_UNITS = os.environ.get("TEMPERATURE_UNITS", "imperial")
FORECAST_DAYS = int(os.environ.get("FORECAST_DAYS", "3"))

# --- Web portal ---
WEB_PORT = int(os.environ.get("WEB_PORT", "80"))


def web_portal_url(hostname: str) -> str:
    """LAN URL for the web portal (omits :80)."""
    name = (hostname or "raspberrypi").split(".")[0].strip() or "raspberrypi"
    host = f"{name}.local"
    if WEB_PORT == 80:
        return f"http://{host}"
    return f"http://{host}:{WEB_PORT}"


# --- Display & units ---
DISPLAY_WIDTH = int(os.environ.get("DISPLAY_WIDTH", "720"))
DISPLAY_HEIGHT = int(os.environ.get("DISPLAY_HEIGHT", "720"))
DISPLAY_ROTATION = int(os.environ.get("DISPLAY_ROTATION", "90")) % 360
if DISPLAY_ROTATION not in (0, 90, 180, 270):
    DISPLAY_ROTATION = round(DISPLAY_ROTATION / 90) * 90 % 360
DISPLAY_FULLSCREEN = _bool(os.environ.get("DISPLAY_FULLSCREEN", "True"))
# Flight detail: show airline logo when no aircraft photo (False = photo-only / text).
SHOW_AIRLINE_LOGOS = _bool(os.environ.get("SHOW_AIRLINE_LOGOS", "False"))

# --- AIS vessel radar declutter (config.h) ---
# One-line vessel name only (no type/speed); never show MMSI as a label.
VESSEL_SHORT_TAGS = _bool(os.environ.get("VESSEL_SHORT_TAGS", "True"))
# Hide anchored/moored / near-zero SOG vessels from the radar entirely.
VESSEL_HIDE_PARKED = _bool(os.environ.get("VESSEL_HIDE_PARKED", "True"))
# Dim parked icons; keep moving ships brighter (when parked are still shown).
VESSEL_HIERARCHY = _bool(os.environ.get("VESSEL_HIERARCHY", "True"))
# Label policy: all_labels | moving_only | icons_only
_raw_density = (os.environ.get("VESSEL_DENSITY_MODE", "moving_only") or "moving_only").strip().lower()
if _raw_density in ("all", "all_labels", "labels"):
    VESSEL_DENSITY_MODE = "all_labels"
elif _raw_density in ("icons", "icons_only", "icon"):
    VESSEL_DENSITY_MODE = "icons_only"
else:
    VESSEL_DENSITY_MODE = "moving_only"
# SOG below this (knots) counts as parked when nav status is unknown.
VESSEL_PARKED_SOG_KT = float(os.environ.get("VESSEL_PARKED_SOG_KT", "0.5"))


def square_framebuffer_side() -> int:
    """Square draw-buffer size for the round touch UI (refined after display init)."""
    if DISPLAY_WIDTH == DISPLAY_HEIGHT:
        return DISPLAY_WIDTH
    # Legacy rectangular env — guess from config; app.py clamps to the real display.
    if DISPLAY_ROTATION in (90, 270):
        side = max(DISPLAY_WIDTH, DISPLAY_HEIGHT)
    else:
        side = min(DISPLAY_WIDTH, DISPLAY_HEIGHT)
    logger.warning(
        "DISPLAY_WIDTH (%d) != DISPLAY_HEIGHT (%d). Set both to your panel resolution "
        "(e.g. 720×720) in /etc/flightscnr.env. Provisional framebuffer: %d×%d.",
        DISPLAY_WIDTH,
        DISPLAY_HEIGHT,
        side,
        side,
    )
    return side
BUTTONS_DIR = os.environ.get("BUTTONS_DIR", "").strip()
SDL_VIDEODRIVER = os.environ.get("SDL_VIDEODRIVER", "")

DISTANCE_UNITS = os.environ.get("DISTANCE_UNITS", "metric")
CLOCK_FORMAT = os.environ.get("CLOCK_FORMAT", "24hr")
BRIGHTNESS = int(os.environ.get("BRIGHTNESS", "100"))
BRIGHTNESS_NIGHT = int(os.environ.get("BRIGHTNESS_NIGHT", "50"))
NIGHT_BRIGHTNESS = _bool(os.environ.get("NIGHT_BRIGHTNESS", "False"))
NIGHT_START = os.environ.get("NIGHT_START", "22:00")
NIGHT_END = os.environ.get("NIGHT_END", "06:00")

# --- Flight filtering (altitude in feet) ---
_raw_max_alt = os.environ.get("MAX_HEIGHT", os.environ.get("MAX_ALTITUDE", "100000"))
MAX_ALTITUDE_FT = int(_raw_max_alt)
MAX_HEIGHT = MAX_ALTITUDE_FT  # alias used in .env / UI wording
_raw_min_alt = os.environ.get("MIN_HEIGHT", os.environ.get("MIN_ALTITUDE", "0"))
MIN_ALTITUDE = int(_raw_min_alt)
MIN_HEIGHT = MIN_ALTITUDE  # alias used in .env / UI wording


def passes_altitude_filter(alt_ft) -> bool:
    """True if aircraft altitude is at or above MIN_HEIGHT and below MAX_HEIGHT."""
    if alt_ft is None:
        return MIN_ALTITUDE <= 0
    try:
        alt = int(alt_ft)
    except (TypeError, ValueError):
        return MIN_ALTITUDE <= 0
    return MIN_ALTITUDE <= alt < MAX_ALTITUDE_FT
JOURNEY_CODE_SELECTED = _require("JOURNEY_CODE_SELECTED")
STATS_LOG_DAYS = int(os.environ.get("STATS_LOG_DAYS", "0"))
_raw_filler = os.environ.get("JOURNEY_BLANK_FILLER", "").strip()
JOURNEY_BLANK_FILLER = f" {_raw_filler} " if _raw_filler else " ? "
SPEED_UNITS = os.environ.get("SPEED_UNITS", "metric")

# --- Logging & notifications ---
EMAIL = os.environ.get("EMAIL", "")
MAX_FARTHEST = int(os.environ.get("MAX_FARTHEST", "3"))
MAX_CLOSEST = int(os.environ.get("MAX_CLOSEST", "3"))
