import os
import json
import math
import socket
import logging
import requests
from time import sleep, time
from datetime import datetime
from threading import Thread, Lock

from utilities.airline_branding import (
    AMBIGUOUS_REGIONALS,
    IATA_TO_ICAO,
    MARKETING_BRANDS,
    marketing_brand_name,
    resolve_logo_icao,
)
from utilities.fr24_client import FR24Client, LiveFlight
from httpx import ConnectError, TimeoutException

logger = logging.getLogger(__name__)

from config import (
    DISTANCE_UNITS,
    CLOCK_FORMAT,
    MAX_FARTHEST,
    MAX_CLOSEST,
)

from setup import email_alerts

# Lazy imports — folium (used by map_generator) may not be installed in test envs
map_generator = None
upload_helper = None

def _ensure_map_imports():
    global map_generator, upload_helper
    if map_generator is None:
        from web import map_generator as _mg, upload_helper as _uh
        map_generator = _mg
        upload_helper = _uh

# Optional config values
try:
    from config import MIN_ALTITUDE
except (ImportError, ModuleNotFoundError, NameError):
    MIN_ALTITUDE = 0

try:
    from config import (
        ZONE_HOME, LOCATION_HOME, location_configured,
        SEARCH_RADIUS_NM, ADSB_ENABLED,
    )
    ZONE_DEFAULT = ZONE_HOME
    LOCATION_DEFAULT = LOCATION_HOME
except (ImportError, ModuleNotFoundError, NameError):
    SEARCH_RADIUS_NM = 15
    ADSB_ENABLED = True
    ZONE_DEFAULT = {"tl_y": 0.0, "tl_x": 0.0, "br_y": 0.0, "br_x": 0.0}
    LOCATION_DEFAULT = [0.0, 0.0]

    def location_configured():
        return False


# Local databases for offline lookups (no API calls needed)
try:
    from utilities.airports import get_airport_coords as _local_airport_coords
    _HAS_LOCAL_AIRPORTS = True
except ImportError:
    _HAS_LOCAL_AIRPORTS = False

try:
    from utilities.airlines import get_airline_name as _local_airline_name
    _HAS_LOCAL_AIRLINES = True
except ImportError:
    _HAS_LOCAL_AIRLINES = False

# Constants
RETRIES = 3
MAX_FLIGHT_LOOKUP = 5
MAX_ALTITUDE = 100000
EARTH_RADIUS_M = 3958.8
ADSBDB_BASE = "https://api.adsbdb.com"

# Helicopter types — set owner_icao to "HELI" for helicopter logo display
HELICOPTER_TYPES = {
    "S76", "EC35", "EC55", "EC30", "A109", "A139", "A169",
    "B06", "B407", "B429", "R44", "R66", "R22",
    "AS50", "AS55", "AS65", "H60", "BK17", "MD52", "MD50",
    "S92", "AW13", "AW16", "AW10", "B212", "B412",
    "EC45", "EC75", "S61", "S70", "H500", "BALL",
}

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

# Writable data directory — outside home dir to avoid systemd ProtectHome issues
DATA_DIR = os.environ.get("FLIGHTSCNR_DATA_DIR", "/var/lib/flightscnr")
os.makedirs(DATA_DIR, exist_ok=True)

LOG_FILE = os.path.join(DATA_DIR, "close.txt")
LOG_FILE_FARTHEST = os.path.join(DATA_DIR, "farthest.txt")
TRACKED_FILE = os.path.join(DATA_DIR, "tracked_flight.json")
COUNTER_FILE = os.path.join(DATA_DIR, "flight_counter.json")
MAPS_DIR = os.path.join(DATA_DIR, "maps")
os.makedirs(MAPS_DIR, exist_ok=True)
ROUTE_AUDIT_LOG = os.path.join(DATA_DIR, "route_audit.log")

HOSTNAME = socket.gethostname()

# In-memory caches for adsbdb lookups (GA aircraft owner info)
_aircraft_cache = {}  # registration -> {data, ts}
_CACHE_TTL = 3600     # 1 hour
_CACHE_MAX_SIZE = 500  # Evict oldest entries beyond this


# Utility Functions

def safe_load_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, OSError) as e:
        if isinstance(e, (PermissionError, OSError)):
            logger.warning(f"Permission denied reading {path} — attempting to fix")
            try:
                os.chmod(path, 0o666)
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, list) else []
            except Exception:
                pass
        return []


def safe_write_json(path: str, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        # Ensure the file is readable/writable by all users
        os.chmod(path, 0o666)
    except PermissionError:
        logger.warning(f"Permission denied writing {path} — attempting to fix")
        try:
            os.chmod(path, 0o666)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e2:
            logger.error(f"Cannot write {path}: {e2}")


def ordinal(n: int):
    return f"{n}{'tsnrhtdd'[(n//10 % 10 != 1) * (n % 10 < 4) * n % 10::4]}"


def haversine(lat1, lon1, lat2, lon2):
    """Distance between two points. Returns miles or km based on DISTANCE_UNITS.
    Returns 0 if any coordinate is None (fixes: haversine guard for None values)."""
    # Guard against None values — use `any(v is None ...)` instead of `not all(...)`
    # because `not all(...)` fails for airports at 0.0 latitude/longitude
    if any(v is None for v in (lat1, lon1, lat2, lon2)):
        return 0
    lat1, lon1 = map(math.radians, (lat1, lon1))
    lat2, lon2 = map(math.radians, (lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (
        math.sin(dlat / 2)**2 +
        math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    miles = EARTH_RADIUS_M * c
    return miles * 1.609344 if DISTANCE_UNITS == "metric" else miles


_MAX_ETA_MINUTES = 72 * 60
_MAX_REMAINING_SECONDS = _MAX_ETA_MINUTES * 60


def _format_time_remaining_minutes(mins_left: int) -> str | None:
    """Format minutes as H:MM or Mm; reject zero and absurd values."""
    if mins_left <= 0 or mins_left > _MAX_ETA_MINUTES:
        return None
    h = mins_left // 60
    m = mins_left % 60
    return f"{h}:{m:02d}" if h > 0 else f"{m}m"


def _plausible_remaining_seconds(secs) -> bool:
    try:
        secs = int(secs)
    except (TypeError, ValueError):
        return False
    return 0 < secs <= _MAX_REMAINING_SECONDS


def _parse_time_remaining_minutes(time_str: str) -> int | None:
    """Parse H:MM or Mm into total minutes; reject absurd values."""
    if not time_str:
        return None
    try:
        if ":" in time_str:
            parts = time_str.split(":")
            mins = int(parts[0]) * 60 + int(parts[1])
        else:
            mins = int(str(time_str).replace("m", ""))
    except (ValueError, IndexError):
        return None
    if mins <= 0 or mins > _MAX_ETA_MINUTES:
        return None
    return mins


def _flight_has_arrived(match, flight_details, fp, dist_remaining) -> bool:
    if getattr(match, "on_ground", False):
        return True
    actual_arrival = (flight_details or {}).get("schedule_info", {}) or {}
    if actual_arrival.get("actual_arrival"):
        return True
    eta = fp.get("eta", 0) or 0
    if not eta or eta > time():
        return False
    near = 3.0 if DISTANCE_UNITS != "metric" else 5.0
    speed = getattr(match, "ground_speed", 0) or 0
    if dist_remaining is not None and dist_remaining <= near:
        return True
    return speed < 60


def estimate_stale_data(last_data):
    data = dict(last_data)
    data["is_live"] = False

    speed_kts = data.get("ground_speed", 0)
    last_ts   = data.get("last_seen_ts")

    if not last_ts:
        return data

    elapsed_hrs  = (time() - last_ts) / 3600
    elapsed_mins = elapsed_hrs * 60

    # --- Time remaining: subtract elapsed time from last known ---
    last_time_str = data.get("time_remaining", "")
    if last_time_str:
        last_mins = _parse_time_remaining_minutes(str(last_time_str))
        if last_mins is not None:
            est_mins = max(0, last_mins - int(elapsed_mins))
            data["time_remaining"] = _format_time_remaining_minutes(est_mins)
        else:
            data["time_remaining"] = None

    # --- Distance remaining: subtract distance covered ---
    last_dist = data.get("dist_remaining")
    if last_dist is not None and speed_kts > 0:
        # Convert knots to display units per hour
        if DISTANCE_UNITS == "metric":
            speed_display = speed_kts * 1.852      # knots -> kph
        else:
            speed_display = speed_kts * 1.15078    # knots -> mph
        dist_covered = speed_display * elapsed_hrs
        data["dist_remaining"] = max(0, last_dist - dist_covered)

    return data


def degrees_to_cardinal(deg):
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int((deg + 22.5) / 45)
    return dirs[idx % 8]


def plane_bearing(flight, home=LOCATION_DEFAULT):
    lat1, lon1 = map(math.radians, home)
    lat2, lon2 = map(math.radians, (flight.latitude, flight.longitude))
    b = math.atan2(
        math.sin(lon2 - lon1) * math.cos(lat2),
        math.cos(lat1) * math.sin(lat2)
        - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    )
    return (math.degrees(b) + 360) % 360


def distance_from_flight_to_home(flight):
    return haversine(
        flight.latitude, flight.longitude,
        LOCATION_DEFAULT[0], LOCATION_DEFAULT[1],
    )


# --- Local database lookups (no API calls needed) ---

def _airport_coords(code):
    """Look up airport coordinates from local database (no API calls).
    Accepts IATA (JFK, LHR) or ICAO (KJFK, EGLL). Returns {lat, lon} or empty dict."""
    if not code:
        return {}
    if _HAS_LOCAL_AIRPORTS:
        return _local_airport_coords(code)
    return {}


def _airline_name_lookup(icao_code):
    """Look up airline name from local database. Returns empty string if not found."""
    if not icao_code:
        return ""
    if _HAS_LOCAL_AIRLINES:
        return _local_airline_name(icao_code)
    return ""


def _evict_aircraft_cache():
    """Evict oldest entries if cache exceeds max size."""
    if len(_aircraft_cache) > _CACHE_MAX_SIZE:
        sorted_keys = sorted(_aircraft_cache, key=lambda k: _aircraft_cache[k]["ts"])
        for k in sorted_keys[:len(_aircraft_cache) - _CACHE_MAX_SIZE]:
            del _aircraft_cache[k]


def _adsbdb_aircraft(registration):
    """Fetch aircraft owner info by registration from adsbdb (free, cached 1hr).
    Used for GA flights (N-numbers) where FR24 has no airline name."""
    cached = _aircraft_cache.get(registration)
    if cached and (time() - cached["ts"]) < _CACHE_TTL:
        return cached["data"]

    url = f"{ADSBDB_BASE}/v0/aircraft/{registration}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 404:
            _aircraft_cache[registration] = {"data": {}, "ts": time()}
            _evict_aircraft_cache()
            return {}
        r.raise_for_status()
        ac = r.json().get("response", {}).get("aircraft")
        if not ac:
            _aircraft_cache[registration] = {"data": {}, "ts": time()}
            _evict_aircraft_cache()
            return {}
        result = {
            "owner": ac.get("registered_owner", ""),
            "type": ac.get("icao_type", ""),
            "manufacturer": ac.get("manufacturer", ""),
            "registration": ac.get("registration", ""),
        }
        _aircraft_cache[registration] = {"data": result, "ts": time()}
        _evict_aircraft_cache()
        return result
    except Exception as e:
        logger.debug(f"adsbdb aircraft error for {registration}: {e}")
        # Cache error for 5 minutes to avoid hammering
        _aircraft_cache[registration] = {"data": {}, "ts": time() - _CACHE_TTL + 300}
        return {}


# --- Audit Logging ---

def _log_route_audit(callsign, aircraft_type, distance, source, origin, destination):
    """Append to route_audit.log with hostname prefix for multi-device monitoring."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    route_str = f"{origin or '?'}->{destination or '?'}"
    line = f"{ts} [{HOSTNAME}] {callsign} {aircraft_type} {distance:.1f} {source} {route_str}\n"
    try:
        with open(ROUTE_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def load_tracked_callsign():
    """Read the tracked callsign from tracked_flight.json."""
    try:
        with open(TRACKED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("callsign", "").strip().upper()
    except (FileNotFoundError, json.JSONDecodeError):
        return ""


def _load_counter_log() -> dict:
    try:
        with open(COUNTER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_counter_log(data: dict) -> None:
    try:
        from config import STATS_LOG_DAYS as max_days
    except (ImportError, AttributeError):
        max_days = 0
    if max_days and max_days > 0:
        from datetime import date, timedelta
        cutoff = str(date.today() - timedelta(days=max_days))
        data = {k: v for k, v in data.items() if k >= cutoff}
    try:
        with open(COUNTER_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        logger.warning(f"Could not save flight counter: {e}")


def log_flight_count(callsign: str, entry: dict | None = None) -> None:
    """Record a unique callsign sighting for the daily flight statistics page."""
    if entry is None:
        entry = {}
    callsign = (callsign or "").strip().upper()
    if not callsign:
        return
    now = datetime.now()
    today = str(now.date())
    now_str = now.strftime("%H:%M:%S")
    log = _load_counter_log()
    if today not in log:
        log[today] = {
            "date": today,
            "count": 0,
            "flights": [],
            "first_seen": now_str,
            "last_seen": now_str,
        }
    seen = {e.get("callsign") for e in log[today].get("flights", [])}
    if callsign not in seen:
        log[today]["flights"].append({
            "callsign": callsign,
            "time": now_str,
            "hour": now.hour,
            "origin": entry.get("origin", ""),
            "dest": entry.get("destination", ""),
            "aircraft": entry.get("plane", ""),
        })
        log[today]["count"] = len(log[today]["flights"])
    log[today]["last_seen"] = now_str
    _save_counter_log(log)


# Logging Closest Flights

def log_flight_data(entry: dict):
    try:
        entry["timestamp"] = email_alerts.get_timestamp()
        lst = safe_load_json(LOG_FILE)

        callsigns = {f.get("callsign"): f for f in lst}
        new_call = entry.get("callsign")
        new_dist = entry.get("distance", float("inf"))
        notify = False

        if new_call in callsigns:
            idx = next(i for i, f in enumerate(lst) if f.get("callsign") == new_call)
            if new_dist < lst[idx].get("distance", float("inf")):
                lst[idx] = entry
            else:
                return
        else:
            lst.append(entry)

        lst.sort(key=lambda x: x.get("distance", float("inf")))
        top_n = lst[:MAX_CLOSEST]

        if new_call not in [f["callsign"] for f in top_n]:
            return

        rank = next(i + 1 for i, f in enumerate(top_n) if f["callsign"] == new_call)

        if new_call not in callsigns:
            notify = True

        safe_write_json(LOG_FILE, top_n)

        if notify:
            _ensure_map_imports()
            html = map_generator.generate_closest_map(top_n, filename="closest.html")
            url = upload_helper.upload_map_to_server(html)
            subject = f"New {ordinal(rank)} Closest Flight - {entry.get('callsign','Unknown')}"
            email_alerts.send_flight_summary(subject, entry, map_url=url)

    except Exception as e:
        print("Failed to log closest flight:", e)


def log_farthest_flight(entry: dict):
    try:
        d_o = entry.get("distance_origin") or -1
        d_d = entry.get("distance_destination") or -1

        if d_o < 0 and d_d < 0:
            return

        reason = "origin" if d_o >= d_d else "destination"
        far = d_o if reason == "origin" else d_d
        airport = entry.get(reason)

        if not airport:
            return

        entry["timestamp"] = email_alerts.get_timestamp()
        entry["reason"] = reason
        entry["farthest_value"] = far
        entry["_airport"] = airport

        lst = safe_load_json(LOG_FILE_FARTHEST)
        airport_map = {f["_airport"]: f for f in lst}

        existing = airport_map.get(airport)
        notify = False
        updated = False

        if existing:
            if (entry.get("distance") or 9e9) < existing.get("distance", 9e9):
                lst = [entry if f["_airport"] == airport else f for f in lst]
                updated = True
            else:
                return
        else:
            if len(lst) >= MAX_FARTHEST:
                if far <= min(f["farthest_value"] for f in lst):
                    return
            lst.append(entry)
            notify = True

        lst.sort(key=lambda x: x["farthest_value"], reverse=True)
        lst = lst[:MAX_FARTHEST]
        safe_write_json(LOG_FILE_FARTHEST, lst)

        if notify or updated:
            _ensure_map_imports()
            html = map_generator.generate_farthest_map(lst, filename="farthest.html")

        if notify:
            _ensure_map_imports()
            url = upload_helper.upload_map_to_server(html)
            rank = next(i for i, f in enumerate(lst) if f["_airport"] == airport) + 1
            cs = entry.get("callsign", "UNKNOWN")
            if rank == 1:
                subject = f"New Farthest Flight ({reason}) - {cs}"
            else:
                subject = f"{ordinal(rank)}-Farthest Flight ({reason}) - {cs}"
            email_alerts.send_flight_summary(subject, entry, reason, map_url=url)

    except Exception as e:
        import traceback
        print("Failed to log farthest flight:", e)
        traceback.print_exc()


# Overhead Class

class Overhead:
    def __init__(self):
        self._api = FR24Client()
        self._lock = Lock()
        self._data = []           # overhead flights
        self._tracked_data = None # tracked flight or None
        self._new_data = False
        self._processing = False
        self._grab_seq = 0
        self._tracked_was_live = False       # was the flight live last poll?
        self._tracked_miss_count = 0         # consecutive polls with no result
        self._TRACKED_MISS_THRESHOLD = 3     # fallback miss threshold (no ETA)
        self._tracked_last_callsign = ""     # last callsign we polled for
        self._tracked_last_eta = None        # last known estimated arrival (unix ts)
        self._tracked_last_data = None       # last known good tracked data
        self._tracked_schedule_cache = {}    # callsign -> AirLabs schedule result (or None)
        self._first_flight_logged = False    # log first flight details as JSON
        self._cycle_count = 0               # total grab_data cycles
        self._total_flights_seen = 0        # lifetime flight count

    def _log_pipeline_summary(self, stats: dict):
        """
        Log a pretty summary of the data pipeline cycle.

        Displays: API calls made, data sources used, flights processed,
        helicopters detected, and data enrichment statistics.
        """
        elapsed = stats.get("elapsed_ms", 0)
        self._cycle_count += 1
        self._total_flights_seen += stats.get("flights_processed", 0)

        lines = [
            "",
            "┌─────────────────────────────────────────────────────────",
            f"│ 🛩️  Pipeline Cycle #{self._cycle_count}  ({elapsed:.0f}ms)",
            "├─────────────────────────────────────────────────────────",
            f"│ FR24 API Status:       {'✓ OK' if self._api.fr24_ok else '✗ UNREACHABLE'}",
            f"│ Zone flights (raw):    {stats.get('zone_raw', 0)}",
            f"│ After altitude filter: {stats.get('zone_filtered', 0)} "
            f"(min={MIN_ALTITUDE}ft, max={MAX_ALTITUDE}ft)",
            f"│ Flights processed:     {stats.get('flights_processed', 0)}",
            f"│ Details fetched (API): {stats.get('details_fetched', 0)}",
            f"│ Details from cache:    {stats.get('details_cached', 0)}",
            "├─── Data Sources ───────────────────────────────────────",
            f"│ Local airports used:   {stats.get('airport_lookups', 0)} "
            f"({'✓ loaded' if _HAS_LOCAL_AIRPORTS else '✗ not available'})",
            f"│ Local airlines used:   {stats.get('airline_lookups', 0)} "
            f"({'✓ loaded' if _HAS_LOCAL_AIRLINES else '✗ not available'})",
            f"│ adsbdb GA lookups:     {stats.get('adsbdb_lookups', 0)}",
            f"│ adsb.fi aircraft:      {stats.get('adsb_added', 0)}",
            f"│ adsb.fi refreshed:     {stats.get('adsb_updated', 0)}",
            f"│ Below min height:     {stats.get('altitude_dropped', 0)}",
            f"│ Helicopter detected:   {stats.get('helicopters', 0)}",
        ]

        # Tracked flight info
        tracked = stats.get("tracked_status", "")
        if tracked:
            lines.append("├─── Tracked Flight ─────────────────────────────────────")
            lines.append(f"│ Status: {tracked}")
            if stats.get("tracked_callsign"):
                lines.append(f"│ Callsign: {stats['tracked_callsign']}")

        # Flight details table
        flights = stats.get("flight_details", [])
        if flights:
            lines.append("├─── Overhead Flights ───────────────────────────────────")
            lines.append("│  #  Callsign   Type  Route         Dist   Source")
            lines.append("│ ─── ────────── ───── ───────────── ────── ──────────")
            for i, fd in enumerate(flights, 1):
                cs = fd.get("callsign", "?")[:9].ljust(9)
                ac = fd.get("plane", "?")[:5].ljust(5)
                orig = fd.get("origin", "?")[:3]
                dest = fd.get("destination", "?")[:3]
                route = f"{orig}→{dest}".ljust(13)
                dist = f"{fd.get('distance', 0):.1f}".rjust(5)
                src = fd.get("data_source", "fr24")[:10]
                lines.append(f"│ {i:>2}  {cs} {ac} {route} {dist}  {src}")

        lines.append("├─── Lifetime Stats ─────────────────────────────────────")
        lines.append(f"│ Total cycles:         {self._cycle_count}")
        lines.append(f"│ Total flights seen:   {self._total_flights_seen}")
        lines.append(f"│ Aircraft cache size:  {len(_aircraft_cache)}")
        lines.append("└─────────────────────────────────────────────────────────")

        logger.info("\n".join(lines))

    def grab_data(self):
        Thread(target=self._grab, daemon=True).start()

    def safe_get(self, d, *keys, default=None):
        for key in keys:
            if not d or not isinstance(d, dict):
                return default
            d = d.get(key)
        return d if d is not None else default

    def _grab(self):
        with self._lock:
            self._processing = True

        overhead_data = []
        tracked_data = None
        _grab_start = time()

        # Pipeline stats for diagnostic logging
        stats = {
            "zone_raw": 0,
            "zone_filtered": 0,
            "flights_processed": 0,
            "details_fetched": 0,
            "details_cached": 0,
            "airport_lookups": 0,
            "airline_lookups": 0,
            "adsbdb_lookups": 0,
            "adsb_added": 0,
            "adsb_updated": 0,
            "helicopters": 0,
            "tracked_status": "",
            "tracked_callsign": "",
            "flight_details": [],
        }

        try:
            # --- STEP 1: Check zone for overhead flights ---
            if not location_configured():
                logger.error(
                    "Location not configured — set HOME_LAT/HOME_LON or zone corners "
                    "in /etc/flightscnr.env"
                )
                flights = []
            else:
                flights = self._api.get_flights(bounds=ZONE_DEFAULT)
            stats["zone_raw"] = len(flights)
            flights = [f for f in flights if MIN_ALTITUDE <= f.altitude < MAX_ALTITUDE]
            stats["zone_filtered"] = len(flights)
            flights.sort(key=lambda f: distance_from_flight_to_home(f))
            flights = flights[:MAX_FLIGHT_LOOKUP]

            for f in flights:
                retries = RETRIES
                while retries:
                    try:
                        d = self._api.get_flight_details(f)
                        stats["details_fetched"] += 1

                        if not d:
                            retries -= 1
                            continue

                        # Log first flight details as pretty JSON for debugging
                        if not self._first_flight_logged:
                            self._first_flight_logged = True
                            logger.info(
                                "First flight API response:\n%s",
                                json.dumps(d, indent=2, default=str),
                            )

                        # Aircraft type from details, fallback to live feed
                        plane = self.safe_get(d, "aircraft", "model", "code", default="") or f.aircraft_code or ""

                        # Airline name: try local database first, then FR24's registered_owners
                        flight_number = self.safe_get(d, "schedule_info", "flight_number", default="")
                        airline_name = self.safe_get(d, "aircraft_info", "registered_owners", default="")

                        callsign = f.callsign or ""
                        airline_icao = resolve_logo_icao(
                            operator_icao=f.airline_icao or "",
                            flight_number=flight_number,
                            callsign=callsign,
                        )
                        owner_icao = airline_icao

                        brand = marketing_brand_name(flight_number) or marketing_brand_name(callsign)
                        if brand:
                            airline_name = brand
                        else:
                            local_airline = _airline_name_lookup(owner_icao)
                            if local_airline:
                                airline_name = local_airline
                            stats["airline_lookups"] += 1

                        # Helicopter detection — override owner_icao for logo display
                        if plane in HELICOPTER_TYPES:
                            owner_icao = "HELI"
                            stats["helicopters"] += 1

                        # GA airplane icon for N-number flights (no airline ICAO prefix)
                        elif not owner_icao and f.registration and f.registration.startswith("N") and f.registration[1:2].isdigit():
                            owner_icao = "GA"

                        # GA owner lookup for N-number aircraft with no airline
                        if (not airline_name and f.registration
                                and f.registration.startswith("N")
                                and f.registration[1:2].isdigit()):
                            stats["adsbdb_lookups"] += 1
                            ac_info = _adsbdb_aircraft(f.registration)
                            if ac_info.get("owner"):
                                airline_name = ac_info["owner"]
                                if airline_name == airline_name.upper():
                                    airline_name = airline_name.title()

                        # Livery note: when painted_as_id differs from operated_by_id
                        painted_as_id = self.safe_get(d, "schedule_info", "painted_as_id", default=0) or 0
                        operated_by_id = self.safe_get(d, "schedule_info", "operated_by_id", default=0) or 0
                        has_special_livery = (painted_as_id != 0 and operated_by_id != 0 and painted_as_id != operated_by_id)

                        origin = f.origin_airport_iata or ""
                        destination = f.destination_airport_iata or ""

                        t = self.safe_get(d, "time", default={})
                        time_sched_dep = self.safe_get(t, "scheduled", "departure")
                        time_sched_arr = self.safe_get(t, "scheduled", "arrival")
                        time_real_dep = self.safe_get(t, "real", "departure")
                        time_est_arr = self.safe_get(t, "estimated", "arrival")

                        # Airport coordinates from local database (no API calls)
                        origin_lat = None
                        origin_lon = None
                        dest_lat = None
                        dest_lon = None

                        if origin:
                            coords = _airport_coords(origin)
                            origin_lat = coords.get("lat")
                            origin_lon = coords.get("lon")
                            if origin_lat is not None:
                                stats["airport_lookups"] += 1
                        if destination:
                            coords = _airport_coords(destination)
                            dest_lat = coords.get("lat")
                            dest_lon = coords.get("lon")
                            if dest_lat is not None:
                                stats["airport_lookups"] += 1

                        # Calculate distances: prefer local airport coords, fallback to flight_progress
                        fp = self.safe_get(d, "flight_progress") or {}
                        traversed_km = fp.get("traversed_distance", 0) or 0
                        remaining_km = fp.get("remaining_distance", 0) or 0

                        # Use local airport coords for distance if available
                        if origin_lat is not None and origin_lon is not None:
                            dist_o = haversine(f.latitude, f.longitude, origin_lat, origin_lon)
                        elif traversed_km:
                            # Fallback to flight_progress (values are in km)
                            if DISTANCE_UNITS == "metric":
                                dist_o = traversed_km
                            else:
                                dist_o = traversed_km / 1.609344
                        else:
                            dist_o = 0

                        if dest_lat is not None and dest_lon is not None:
                            dist_d = haversine(f.latitude, f.longitude, dest_lat, dest_lon)
                        elif remaining_km:
                            # Fallback to flight_progress (values are in km)
                            if DISTANCE_UNITS == "metric":
                                dist_d = remaining_km
                            else:
                                dist_d = remaining_km / 1.609344
                        else:
                            dist_d = 0

                        # Extract airborne trail points only (alt > 0)
                        raw_trail = self.safe_get(d, "trail", default=[]) or []
                        trail = [
                            [pt["lat"], pt["lng"]]
                            for pt in raw_trail
                            if isinstance(pt, dict) and pt.get("alt", 0) > 0
                        ]

                        # Determine livery note text (only if special and short)
                        livery_note = ""
                        if has_special_livery and airline_name:
                            livery_note = "special livery"

                        entry = {
                            "airline": airline_name,
                            "plane": plane,
                            "flight_number": flight_number,
                            "origin": origin,
                            "origin_latitude": origin_lat,
                            "origin_longitude": origin_lon,
                            "destination": destination,
                            "destination_latitude": dest_lat,
                            "destination_longitude": dest_lon,
                            "plane_latitude": f.latitude,
                            "plane_longitude": f.longitude,
                            "owner_iata": f.airline_iata or "N/A",
                            "owner_icao": owner_icao,
                            "airline_icao": airline_icao,
                            "time_scheduled_departure": time_sched_dep,
                            "time_scheduled_arrival": time_sched_arr,
                            "time_real_departure": time_real_dep,
                            "time_estimated_arrival": time_est_arr,
                            "vertical_speed": f.vertical_speed,
                            "altitude": f.altitude,
                            "ground_speed": f.ground_speed or 0,
                            "heading": f.heading or 0,
                            "callsign": callsign,
                            "distance_origin": dist_o,
                            "distance_destination": dist_d,
                            "distance": distance_from_flight_to_home(f),
                            "direction": degrees_to_cardinal(plane_bearing(f)),
                            "trail": trail,
                            "livery_note": livery_note,
                        }

                        overhead_data.append(entry)
                        stats["flights_processed"] += 1

                        # Track flight details for pipeline summary
                        stats["flight_details"].append({
                            "callsign": callsign,
                            "plane": plane,
                            "origin": origin,
                            "destination": destination,
                            "distance": entry["distance"],
                            "data_source": "fr24_grpc",
                        })

                        # Audit log
                        _log_route_audit(callsign, plane, entry["distance"], "fr24_grpc", origin, destination)

                        log_flight_data(entry)
                        log_farthest_flight(entry)
                        log_flight_count(callsign, entry)
                        break

                    except Exception as e:
                        retries -= 1
                        if retries == 0:
                            logger.warning(f"Failed to get details for {f.callsign}: {e}")

            # --- STEP 1b: ADS-B live positions (adsb.fi — free, used by FlightScnr) ---
            adsb_entries: list[dict] = []
            by_callsign: dict[str, dict] = {}
            if ADSB_ENABLED and location_configured():
                from utilities.adsb_client import fetch_aircraft_entries
                adsb_entries = fetch_aircraft_entries(
                    LOCATION_DEFAULT[0],
                    LOCATION_DEFAULT[1],
                    SEARCH_RADIUS_NM,
                    MIN_ALTITUDE,
                )
                _LIVE_FIELDS = (
                    "plane_latitude", "plane_longitude", "altitude",
                    "heading", "ground_speed", "vertical_speed",
                )
                for existing in overhead_data:
                    cs = (existing.get("callsign") or "").strip().upper()
                    if cs:
                        by_callsign[cs] = existing

                for entry in adsb_entries:
                    cs = (entry.get("callsign") or "").strip().upper()
                    if cs and cs in by_callsign:
                        for field in _LIVE_FIELDS:
                            by_callsign[cs][field] = entry[field]
                        stats["adsb_updated"] += 1
                        continue
                    overhead_data.append(entry)
                    stats["adsb_added"] += 1
                    if cs:
                        by_callsign[cs] = entry
                        log_flight_count(cs, entry)

            # --- STEP 2: Tracked flight (always check; display shows it when clock is up) ---
            tracked_callsign = load_tracked_callsign()
            if tracked_callsign:
                stats["tracked_callsign"] = tracked_callsign

                # If callsign changed, reset all state — new flight being tracked
                if tracked_callsign != self._tracked_last_callsign:
                    self._tracked_last_callsign = tracked_callsign
                    self._tracked_was_live = False
                    self._tracked_miss_count = 0
                    self._tracked_last_eta = None
                    self._tracked_last_data = None
                    self._tracked_schedule_cache.clear()

                tracked_data = self._grab_tracked(tracked_callsign, zone_flights=flights)

                if tracked_data:
                    # Flight found — reset miss counter, store latest ETA and data
                    self._tracked_was_live = True
                    self._tracked_miss_count = 0
                    self._tracked_last_eta = tracked_data.get("time_estimated_arrival")
                    self._tracked_last_data = tracked_data
                else:
                    if self._tracked_was_live:
                        # Was live before, now missing
                        now_ts = time()
                        eta    = self._tracked_last_eta

                        if eta is not None:
                            mins_since_eta = (now_ts - eta) / 60
                            if mins_since_eta > 0:
                                # ETA has passed — use miss counter to confirm
                                # before wiping (avoids false wipe on brief API hiccup)
                                self._tracked_miss_count += 1
                                if self._tracked_miss_count >= self._TRACKED_MISS_THRESHOLD:
                                    self._do_auto_wipe()
                                elif self._tracked_last_data:
                                    tracked_data = estimate_stale_data(self._tracked_last_data)
                            else:
                                # ETA still in future — oceanic gap, serve estimated data
                                # Don't reset miss counter (preserve accumulation for post-ETA)
                                if self._tracked_last_data:
                                    tracked_data = estimate_stale_data(self._tracked_last_data)
                        else:
                            # No ETA data — fall back to miss counter
                            self._tracked_miss_count += 1
                            if self._tracked_miss_count >= self._TRACKED_MISS_THRESHOLD:
                                self._do_auto_wipe()
                            elif self._tracked_last_data:
                                tracked_data = estimate_stale_data(self._tracked_last_data)
                    else:
                        # Never been live — try AirLabs schedule
                        # Cache successful results; retry on failure (airlabs module has 5-min TTL)
                        sched = self._tracked_schedule_cache.get(tracked_callsign)
                        if sched is None:
                            from utilities.airlabs import get_flight_schedule
                            sched = get_flight_schedule(tracked_callsign)
                            if sched:
                                self._tracked_schedule_cache[tracked_callsign] = sched
                        if sched:
                            # Convert callsign to ICAO for logo lookup (UA353 → UAL353)
                            sched_cs = tracked_callsign
                            if len(sched_cs) >= 3 and sched_cs[:2] in IATA_TO_ICAO and sched_cs[2:3].isdigit():
                                icao_pfx = IATA_TO_ICAO.get(sched_cs[:2])
                                if icao_pfx:
                                    sched_cs = icao_pfx + sched_cs[2:]
                            sched_number = sched.get("flight_number", tracked_callsign)
                            airline_icao = resolve_logo_icao(
                                operator_icao=sched_cs[:3] if len(sched_cs) >= 3 and sched_cs[:3].isalpha() else "",
                                flight_number=sched_number,
                                callsign=sched_cs,
                            )
                            tracked_data = {
                                "callsign": sched_cs,
                                "number": sched_number,
                                "flight_number": sched_number,
                                "airline_name": marketing_brand_name(sched_number) or "",
                                "owner_icao": airline_icao,
                                "airline_icao": airline_icao,
                                "is_live": False,
                                "is_scheduled": True,
                                "origin": sched.get("origin", ""),
                                "destination": sched.get("destination", ""),
                                "dep_time": sched.get("dep_time", ""),
                                "arr_time": sched.get("arr_time", ""),
                                "schedule_status": sched.get("status", ""),
                                "aircraft_type": "",
                                "altitude": 0,
                                "ground_speed": 0,
                                "heading": 0,
                                "vertical_speed": 0,
                                "dist_remaining": None,
                                "total_distance": None,
                                "time_remaining": None,
                                "latitude": None,
                                "longitude": None,
                                "last_seen_ts": 0,
                                "dest_lat": 0,
                                "dest_lon": 0,
                            }

            # Keep schedule cache even after flight goes live — arr_time_utc
            # is used as reality check to prevent premature auto-wipe when
            # a taxiing plane briefly appears then drops from the feed.

            # Update tracked status for pipeline summary
            if tracked_data:
                if tracked_data.get("is_live"):
                    stats["tracked_status"] = "LIVE"
                elif tracked_data.get("is_scheduled"):
                    stats["tracked_status"] = "SCHEDULED"
                else:
                    stats["tracked_status"] = "ESTIMATED (stale)"
            elif stats.get("tracked_callsign"):
                stats["tracked_status"] = "NOT FOUND"
            else:
                stats["tracked_status"] = ""

            # Drop anything below MIN_HEIGHT before display / web UI
            try:
                from config import passes_altitude_filter
                before = len(overhead_data)
                overhead_data = [
                    e for e in overhead_data
                    if passes_altitude_filter(e.get("altitude"))
                ]
                stats["altitude_dropped"] = before - len(overhead_data)
            except ImportError:
                stats["altitude_dropped"] = 0

            # --- Pipeline Summary ---
            stats["elapsed_ms"] = (time() - _grab_start) * 1000
            self._log_pipeline_summary(stats)

            with self._lock:
                self._data = overhead_data
                self._tracked_data = tracked_data
                self._new_data = True

        except (ConnectionError, ConnectError, TimeoutException, OSError) as e:
            logger.warning(f"Overhead: Network error during _grab: {type(e).__name__}: {e}")
            with self._lock:
                self._data = []
                self._tracked_data = None
                self._new_data = True
        except Exception as e:
            logger.error(f"Overhead: Unexpected error in _grab: {type(e).__name__}: {e}", exc_info=True)
            with self._lock:
                self._data = []
                self._tracked_data = None
                self._new_data = True
        finally:
            with self._lock:
                self._processing = False
                self._grab_seq += 1

    def _do_auto_wipe(self):
        """Wipe tracked_flight.json and reset all tracking state."""
        try:
            with open(TRACKED_FILE, "w", encoding="utf-8") as f:
                json.dump({"callsign": ""}, f)
            print("Tracked flight ended — auto-cleared.")
        except Exception as e:
            print(f"Failed to auto-clear tracked flight: {e}")
        self._tracked_was_live = False
        self._tracked_miss_count = 0
        self._tracked_last_eta = None
        self._tracked_last_data = None
        self._tracked_last_callsign = ""

    def _grab_tracked(self, flight_input, zone_flights=None):
        flight_input = flight_input.strip().upper()

        # Convert IATA format (UA353, B6555) to ICAO (UAL353, JBU555) for gRPC filter
        if len(flight_input) >= 3 and flight_input[:2] in IATA_TO_ICAO and flight_input[2:3].isdigit():
            icao_prefix = IATA_TO_ICAO.get(flight_input[:2])
            if icao_prefix:
                flight_input = icao_prefix + flight_input[2:]

        match = None

        try:
            # Always fetch a fresh live position — the zone feed cache can be ~90s stale.
            match = self._api.find_by_callsign(flight_input)

            if not match:
                return None

            flight_details = self._api.get_flight_details(match)
            match.set_flight_details(flight_details)

            # Use flight_progress from the API for distances (values are in KM)
            fp = self.safe_get(flight_details, "flight_progress") or {}
            remaining_km = fp.get("remaining_distance", 0) or 0
            total_km = fp.get("great_circle_distance", 0) or 0
            eta = fp.get("eta", 0) or 0

            # Look up airport coordinates from local database for distance calculations
            origin_code = match.origin_airport_iata or ""
            dest_code = match.destination_airport_iata or ""
            dest_coords = _airport_coords(dest_code)
            origin_coords = _airport_coords(origin_code)

            dest_lat = dest_coords.get("lat")
            dest_lon = dest_coords.get("lon")

            # Calculate distance remaining: prefer local airport coords
            if dest_lat is not None and dest_lon is not None:
                dist_remaining = haversine(match.latitude, match.longitude, dest_lat, dest_lon)
            elif remaining_km:
                # Fallback to flight_progress (values are in km)
                if DISTANCE_UNITS == "metric":
                    dist_remaining = remaining_km
                else:
                    dist_remaining = remaining_km / 1.609344
            else:
                dist_remaining = None

            # Total distance: prefer local airport coords
            origin_lat = origin_coords.get("lat")
            origin_lon = origin_coords.get("lon")
            if (origin_lat is not None and origin_lon is not None
                    and dest_lat is not None and dest_lon is not None):
                total_distance = haversine(origin_lat, origin_lon, dest_lat, dest_lon)
            elif total_km:
                # Fallback to flight_progress (values are in km)
                if DISTANCE_UNITS == "metric":
                    total_distance = total_km
                else:
                    total_distance = total_km / 1.609344
            else:
                total_distance = None

            # Calculate time remaining from ETA
            time_remaining = None
            now_ts = time()
            has_landed = _flight_has_arrived(match, flight_details, fp, dist_remaining)
            if not has_landed:
                eta = fp.get("eta", 0) or 0
                if eta and eta > now_ts:
                    mins_left = int((eta - now_ts) / 60)
                    time_remaining = _format_time_remaining_minutes(mins_left)
                if not time_remaining:
                    remaining_secs = fp.get("remaining_time", 0) or 0
                    if _plausible_remaining_seconds(remaining_secs):
                        time_remaining = _format_time_remaining_minutes(
                            remaining_secs // 60
                        )
                if not time_remaining and dist_remaining and match.ground_speed:
                    if DISTANCE_UNITS == "metric":
                        dist_nm = dist_remaining * 0.539957
                    else:
                        dist_nm = dist_remaining * 0.868976
                    if dist_nm > 0:
                        hrs_left = dist_nm / match.ground_speed
                        mins_left = int(hrs_left * 60)
                        time_remaining = _format_time_remaining_minutes(mins_left)

            # Time fields for delay colour coding
            time_details = self.safe_get(flight_details, "time") or {}
            time_sched_dep = self.safe_get(time_details, "scheduled", "departure")
            time_sched_arr = self.safe_get(time_details, "scheduled", "arrival")
            time_real_dep  = self.safe_get(time_details, "real", "departure")
            time_est_arr   = self.safe_get(time_details, "estimated", "arrival") or eta or None

            # Airline name: try local database, then FR24
            airline_name = match.airline_name or ""
            if not airline_name:
                airline_icao_code = match.airline_icao or ""
                airline_name = _airline_name_lookup(airline_icao_code)

            # GA owner lookup for N-number aircraft
            if (not airline_name and match.registration
                    and match.registration.startswith("N")
                    and match.registration[1:2].isdigit()):
                ac_info = _adsbdb_aircraft(match.registration)
                if ac_info.get("owner"):
                    airline_name = ac_info["owner"]
                    if airline_name == airline_name.upper():
                        airline_name = airline_name.title()

            aircraft_type = match.aircraft_code or ""
            if not aircraft_type:
                aircraft_type = self.safe_get(flight_details, "aircraft_info", "typecode", default="") or ""

            flight_number = (
                match.number
                or self.safe_get(flight_details, "schedule_info", "flight_number", default="")
                or ""
            )
            airline_icao = resolve_logo_icao(
                operator_icao=match.airline_icao or "",
                flight_number=flight_number,
                callsign=flight_input,
            )
            owner_icao = airline_icao
            brand = marketing_brand_name(flight_number) or marketing_brand_name(flight_input)
            if brand:
                airline_name = brand

            return {
                "callsign": flight_input,
                "number": match.number or flight_input,
                "flight_number": flight_number,
                "airline_name": airline_name,
                "owner_icao": owner_icao,
                "airline_icao": airline_icao,
                "is_live": True,
                "has_landed": has_landed,
                "origin": match.origin_airport_iata or "",
                "destination": match.destination_airport_iata or "",
                "dest_lat": dest_lat or 0,
                "dest_lon": dest_lon or 0,
                "aircraft_type": aircraft_type,
                "altitude": match.altitude,
                "ground_speed": match.ground_speed or 0,
                "heading": match.heading or 0,
                "dist_remaining": dist_remaining,
                "total_distance": total_distance,
                "time_remaining": time_remaining,
                "latitude": match.latitude,
                "longitude": match.longitude,
                "last_seen_ts": time(),
                "vertical_speed": match.vertical_speed or 0,
                "time_scheduled_departure": time_sched_dep,
                "time_scheduled_arrival": time_sched_arr,
                "time_real_departure": time_real_dep,
                "time_estimated_arrival": time_est_arr,
            }

        except Exception as e:
            logger.error(f"Failed to grab tracked flight: {e}")
            return None

    # Properties

    @property
    def new_data(self):
        with self._lock:
            return self._new_data

    @property
    def processing(self):
        with self._lock:
            return self._processing

    @property
    def data(self):
        with self._lock:
            self._new_data = False
            return self._data

    def peek_data(self):
        """Thread-safe snapshot for display refresh without clearing new_data."""
        with self._lock:
            return list(self._data)

    @property
    def grab_seq(self):
        with self._lock:
            return self._grab_seq

    @property
    def tracked_data(self):
        with self._lock:
            return self._tracked_data

    @property
    def data_is_empty(self):
        # FIX: Acquire lock to be consistent with all other properties
        with self._lock:
            return len(self._data) == 0


if __name__ == "__main__":
    o = Overhead()
    o.grab_data()
    while not o.new_data:
        print("processing...")
        sleep(1)
    print("Overhead:", o.data)
    print("Tracked:", o.tracked_data)
