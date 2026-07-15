"""Aircraft alert detection — military, emergency squawk, watch list."""

import json
import logging
import os
import time

from display.round_touch import alert_prefs, geo
from utilities.adsb_client import normalize_squawk

logger = logging.getLogger(__name__)

_SEEN_CAPACITY = 32
_seen_hashes: list[int] = []
_last_beep_ts = 0.0
_BEEP_COOLDOWN_S = 2.0
_rim_flash_until = 0.0
_RIM_FLASH_S = 12.0
_RIM_REFLASH_S = 4.0
_attention_until = 0.0
_ATTENTION_HOLD_S = 20.0
_rim_flash_military = False

# ICAO types listed under military-* icon categories (e.g. Q9 → military-drone).
_ICON_MAPPING_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "assets",
    "aircraft",
    "icons",
    "aircraft-icons.json",
)
_military_type_codes: frozenset[str] | None = None


def _military_type_codes_from_icons() -> frozenset[str]:
    global _military_type_codes
    if _military_type_codes is not None:
        return _military_type_codes
    codes: set[str] = set()
    try:
        with open(_ICON_MAPPING_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        for category, types in (data.get("typeCodeMapping") or {}).items():
            if not str(category).startswith("military-"):
                continue
            for code in types or []:
                key = "".join(str(code).upper().split())
                if key:
                    codes.add(key)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.warning("Could not load military type codes from icons: %s", exc)
    _military_type_codes = frozenset(codes)
    return _military_type_codes


def _hash_callsign(callsign: str) -> int:
    h = 2166136261
    for ch in callsign:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _already_seen(h: int) -> bool:
    return h in _seen_hashes


def _mark_seen(h: int) -> None:
    global _seen_hashes
    _seen_hashes.append(h)
    if len(_seen_hashes) > _SEEN_CAPACITY:
        _seen_hashes = _seen_hashes[-_SEEN_CAPACITY:]


def _normalize_callsign(value) -> str:
    if not value:
        return ""
    return "".join(str(value).upper().split())


def callsign_match_keys(callsign: str) -> frozenset[str]:
    """Callsign aliases for matching FR24 entries to ADS-B (e.g. UA123 → UAL123)."""
    cs = _normalize_callsign(callsign)
    if not cs:
        return frozenset()
    keys = {cs}
    if len(cs) >= 3 and cs[:2].isalpha() and cs[2].isdigit():
        try:
            from utilities.airline_branding import IATA_TO_ICAO

            icao = IATA_TO_ICAO.get(cs[:2])
            if icao:
                keys.add(icao + cs[2:])
        except ImportError:
            pass
    return frozenset(keys)


def _normalize_registration(value) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def flight_identity_keys(flight: dict) -> frozenset[str]:
    """Stable identity keys for FR24 ↔ ADS-B merge (hex, registration, callsign)."""
    keys: set[str] = set()
    hx = (flight.get("icao_hex") or flight.get("hex") or "").strip().upper().replace("0X", "")
    if len(hx) >= 6:
        keys.add(f"hex:{hx}")
    reg = _normalize_registration(flight.get("registration"))
    if reg:
        keys.add(f"reg:{reg}")
        for cs in callsign_match_keys(reg):
            keys.add(f"cs:{cs}")
    for cs in callsign_match_keys(flight.get("callsign")):
        keys.add(f"cs:{cs}")
        # ADS-B often puts the N-number in the flight/callsign field.
        if len(cs) >= 2 and cs[0] == "N" and cs[1].isdigit():
            keys.add(f"reg:{cs}")
    return frozenset(keys)


def flights_share_identity(a: dict, b: dict) -> bool:
    left = flight_identity_keys(a)
    right = flight_identity_keys(b)
    return bool(left and right and (left & right))


ADSB_ALERT_FIELDS = ("squawk", "db_flags")


def merge_live_fields(target: dict, source: dict, fields: tuple[str, ...]) -> None:
    """Copy live/ADS-B fields from source onto target."""
    for field in fields:
        if field not in source:
            continue
        value = source[field]
        if field in ("squawk", "callsign", "registration", "icao_hex", "plane") and not value:
            continue
        target[field] = value


def dedupe_flights(flights: list[dict], *, threshold_km: float = 1.2) -> list[dict]:
    """Collapse FR24 + ADS-B duplicates (identity and/or nearby position)."""

    def richness(flight: dict) -> int:
        score = 0
        if flight.get("origin") or flight.get("destination"):
            score += 10
        if flight.get("airline"):
            score += 3
        if flight.get("data_source") != "adsb_fi":
            score += 5
        if flight.get("squawk"):
            score += 1
        if flight.get("db_flags"):
            score += 1
        if flight.get("icao_hex"):
            score += 2
        if flight.get("registration") or flight.get("callsign"):
            score += 1
        return score

    def _alt_ft(flight: dict) -> float | None:
        try:
            return float(flight.get("altitude"))
        except (TypeError, ValueError):
            return None

    def _are_duplicates(a: dict, b: dict) -> bool:
        if flights_share_identity(a, b):
            return True
        lat = a.get("plane_latitude")
        lon = a.get("plane_longitude")
        elat = b.get("plane_latitude")
        elon = b.get("plane_longitude")
        if lat is None or lon is None or elat is None or elon is None:
            return False
        dist = geo.distance_km(lat, lon, elat, elon)
        if dist > threshold_km:
            return False
        # Tight proximity alone is enough (classic dual-feed overlap).
        if dist <= 0.45:
            return True
        # Looser proximity needs a supporting cue so formation pairs stay separate.
        type_a = "".join(str(a.get("plane") or "").upper().split())
        type_b = "".join(str(b.get("plane") or "").upper().split())
        if type_a and type_b and type_a == type_b:
            return True
        alt_a = _alt_ft(a)
        alt_b = _alt_ft(b)
        if alt_a is not None and alt_b is not None and abs(alt_a - alt_b) <= 500:
            return True
        return False

    kept: list[dict] = []
    for flight in flights:
        duplicate = None
        for existing in kept:
            if _are_duplicates(flight, existing):
                duplicate = existing
                break

        if duplicate is None:
            kept.append(flight)
            continue

        live_fields = (
            "plane_latitude", "plane_longitude", "altitude",
            "heading", "ground_speed", "vertical_speed",
            "squawk", "db_flags", "icao_hex", "registration", "callsign", "plane",
        )
        if richness(flight) > richness(duplicate):
            merge_live_fields(flight, duplicate, live_fields)
            # Prefer non-empty identity from either side.
            if not (flight.get("callsign") or "").strip():
                flight["callsign"] = duplicate.get("callsign") or flight.get("callsign")
            if not (flight.get("registration") or "").strip():
                flight["registration"] = duplicate.get("registration") or ""
            kept.remove(duplicate)
            kept.append(flight)
        else:
            merge_live_fields(duplicate, flight, live_fields)
            if not (duplicate.get("callsign") or "").strip():
                duplicate["callsign"] = flight.get("callsign") or duplicate.get("callsign")
            if not (duplicate.get("registration") or "").strip():
                duplicate["registration"] = flight.get("registration") or ""

    return kept


def apply_adsb_alert_fields(flights: list[dict], adsb_entries: list[dict]) -> None:
    """Copy squawk / military flags from ADS-B onto merged flight records."""
    lookup: dict[str, dict] = {}
    for entry in adsb_entries:
        payload = {field: entry.get(field) for field in ADSB_ALERT_FIELDS}
        for key in callsign_match_keys(entry.get("callsign")):
            lookup[key] = payload

    for flight in flights:
        for key in callsign_match_keys(flight.get("callsign")):
            payload = lookup.get(key)
            if not payload:
                continue
            squawk = payload.get("squawk")
            if squawk:
                flight["squawk"] = squawk
            if payload.get("db_flags") is not None:
                flight["db_flags"] = payload.get("db_flags")
            break


def is_military(flight: dict) -> bool:
    try:
        raw = flight.get("db_flags", flight.get("dbFlags"))
        flags = int(raw or 0)
    except (TypeError, ValueError):
        flags = 0
    if flags & 0x01:
        return True
    plane = "".join(str(flight.get("plane") or "").upper().split())
    return bool(plane) and plane in _military_type_codes_from_icons()


def is_emergency_squawk(flight: dict) -> bool:
    squawk = normalize_squawk(flight.get("squawk"))
    return squawk in ("7700", "7600", "7500")


def on_watchlist(flight: dict) -> bool:
    cs = _normalize_callsign(flight.get("callsign"))
    return cs in alert_prefs.watch_callsigns()


def should_alert(flight: dict) -> bool:
    if flight.get("kind") == "vessel":
        return False
    if alert_prefs.military_enabled() and is_military(flight):
        return True
    if alert_prefs.emergency_enabled() and is_emergency_squawk(flight):
        return True
    if on_watchlist(flight):
        return True
    return False


def is_highlighted(flight: dict) -> bool:
    return should_alert(flight)


def is_shown_on_radar(flight: dict) -> bool:
    """True if this aircraft should be drawn when hide-non-alerted is enabled."""
    if flight.get("kind") == "vessel":
        return True
    alert_prefs.reload()
    if not alert_prefs.hide_non_alerted():
        return True
    return is_highlighted(flight)


def pulse_phase() -> bool:
    return int(time.time() * 4) % 2 == 0


def alert_color(flight: dict):
    """Icon fill: military → red; emergency / watch → blue."""
    from display.round_touch import theme

    if alert_prefs.military_enabled() and is_military(flight):
        return theme.ALERT_MILITARY
    if alert_prefs.emergency_enabled() and is_emergency_squawk(flight):
        return theme.ALERT_OTHER
    if on_watchlist(flight):
        return theme.ALERT_OTHER
    return theme.AIRCRAFT


def alert_pulse_color(flight: dict):
    """Alternate pulse color: normal aircraft yellow (not a brighter alert tint)."""
    from display.round_touch import theme

    del flight  # same alternate for all alert types
    return theme.AIRCRAFT


def is_in_range(flight: dict) -> bool:
    lat = flight.get("plane_latitude")
    lon = flight.get("plane_longitude")
    if lat is None or lon is None:
        return False
    return geo.local_offset_km(lat, lon)[2] <= geo.inner_ring_max_km()


def start_rim_flash(*, military: bool = False, duration: float | None = None) -> None:
    """Begin (or restart) the attention rim flash."""
    global _rim_flash_until, _attention_until, _rim_flash_military
    now = time.time()
    dur = _RIM_FLASH_S if duration is None else float(duration)
    _rim_flash_until = now + dur
    _attention_until = now + max(dur, _ATTENTION_HOLD_S)
    _rim_flash_military = bool(military)


def active_alert_flights(flights: list[dict]) -> list[dict]:
    """In-range aircraft that currently match alert prefs."""
    alert_prefs.reload()
    if not alert_prefs.alerts_active():
        return []
    out = []
    for flight in flights:
        if should_alert(flight) and is_in_range(flight):
            out.append(flight)
    return out


def reflash_for_visible_alerts(flights: list[dict]) -> bool:
    """Short rim re-flash when returning to radar with an alert still in view."""
    active = active_alert_flights(flights)
    if not active:
        return False
    military = any(is_military(f) for f in active)
    start_rim_flash(military=military, duration=_RIM_REFLASH_S)
    return True


def check_new_aircraft(flights: list[dict]) -> bool:
    """Log alert when a new in-range alert target appears.

    Returns True if at least one new alert fired (for on-device rim flash).
    A different callsign always re-triggers the rim (seen-set prevents duplicates).
    """
    global _last_beep_ts
    alert_prefs.reload()
    if not alert_prefs.alerts_active():
        return False
    fired = False
    saw_military = False
    for flight in flights:
        if not should_alert(flight):
            continue
        if not is_in_range(flight):
            continue
        cs = _normalize_callsign(flight.get("callsign"))
        if not cs:
            continue
        h = _hash_callsign(cs)
        if _already_seen(h):
            continue
        _mark_seen(h)
        fired = True
        if is_military(flight):
            saw_military = True
        logger.info(
            "ALERT %s mil=%s emrg=%s watch=%s squawk=%s",
            cs,
            is_military(flight),
            is_emergency_squawk(flight),
            on_watchlist(flight),
            flight.get("squawk"),
        )
    if fired:
        now = time.time()
        start_rim_flash(military=saw_military)
        if now - _last_beep_ts >= _BEEP_COOLDOWN_S:
            _last_beep_ts = now
    return fired


def rim_flash_active() -> bool:
    """True while the radar should pulse its outer rim after a new alert."""
    return time.time() < _rim_flash_until


def attention_active() -> bool:
    """True for a bit longer than the bright rim flash (wake / hold attention)."""
    return time.time() < _attention_until or rim_flash_active()


def rim_flash_color():
    """Solid alert rim while pulse is on; None = off (no ring drawn)."""
    from display.round_touch import theme

    if not pulse_phase():
        return None
    if _rim_flash_military:
        return theme.ALERT_MILITARY
    return theme.ALERT_OTHER
