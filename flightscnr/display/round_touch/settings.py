"""Persisted UI settings for round touch display."""

import json
import logging
import os

from display.round_touch import color_presets, theme
logger = logging.getLogger("flightscnr.display")

DATA_DIR = os.environ.get("FLIGHTSCNR_DATA_DIR", "/var/lib/flightscnr")
SETTINGS_PATH = os.path.join(DATA_DIR, "round_touch_settings.json")
_settings_mtime: float | None = None

MIN_HEIGHT_OPTIONS = (0, 500, 1000, 1500)
TRAFFIC_MODES = ("aircraft", "marine", "both")

_defaults = {
    "brightness_percent": 100,
    "distance_units": "km",
    "show_compass_rose": True,
    "show_sweep": True,
    "scale_index": 1,
    "theme_index": color_presets.DEFAULT_THEME_INDEX,
    "clock_12hr": True,
    "auto_timezone": True,
    "min_height_ft": 1000,
    "auto_idle_clock": True,
    "flight_detail_timeout_s": 20,
    "clock_timeout_s": 10,
    # aircraft | marine | both — what the radar shows
    "traffic_mode": "aircraft",
    # Kept in sync with traffic_mode for older readers / portal payloads
    "ais_enabled": False,
}


def _snap_min_height(value) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 1000
    if v in MIN_HEIGHT_OPTIONS:
        return v
    return min(MIN_HEIGHT_OPTIONS, key=lambda opt: abs(opt - v))


def _env_min_height() -> int:
    try:
        from config import MIN_HEIGHT
        return _snap_min_height(MIN_HEIGHT)
    except ImportError:
        return 1000


def _seed_from_env(state: dict) -> None:
    """First-run defaults from /etc/flightscnr.env (not applied after settings are saved)."""
    try:
        from config import DISTANCE_UNITS, MIN_HEIGHT, SEARCH_RADIUS_NM
        from display.round_touch import scale

        state["distance_units"] = "mi" if DISTANCE_UNITS.strip().lower() == "imperial" else "km"
        state["scale_index"] = scale.index_for_radius_nm(SEARCH_RADIUS_NM)
        state["min_height_ft"] = _snap_min_height(MIN_HEIGHT)
    except ImportError:
        pass


def _save(data):
    global _settings_mtime
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp_path = SETTINGS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, SETTINGS_PATH)
        try:
            _settings_mtime = os.path.getmtime(SETTINGS_PATH)
        except OSError:
            _settings_mtime = None
    except OSError as exc:
        logger.warning("Could not save display settings to %s: %s", SETTINGS_PATH, exc)


def _load():
    fresh = not os.path.exists(SETTINGS_PATH)
    if fresh:
        state = dict(_defaults)
        _seed_from_env(state)
        _save(state)
        logger.info("Created display settings at %s", SETTINGS_PATH)
        return state

    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.warning("Could not read %s (%s) — using defaults", SETTINGS_PATH, exc)
        state = dict(_defaults)
        _seed_from_env(state)
        _save(state)
        return state

    state = {**_defaults, **data}
    migrated = False
    if "min_height_ft" not in data:
        state["min_height_ft"] = _env_min_height()
        migrated = True
    else:
        state["min_height_ft"] = _snap_min_height(state["min_height_ft"])
    if "distance_miles" in data and "distance_units" not in data:
        state["distance_units"] = "mi" if data.get("distance_miles") else "km"
        migrated = True
    if "distance_miles" in state:
        del state["distance_miles"]
        migrated = True
    if "distance_units" not in state:
        state["distance_units"] = "km"
        migrated = True
    if "tracked_stats_mode" in state:
        del state["tracked_stats_mode"]
        migrated = True
    if "font_index" in state:
        del state["font_index"]
        migrated = True
    # Migrate legacy ais_enabled bool → traffic_mode enum
    mode = str(state.get("traffic_mode") or "").strip().lower()
    if "traffic_mode" not in data or mode not in TRAFFIC_MODES:
        if "ais_enabled" in data:
            state["traffic_mode"] = "both" if data.get("ais_enabled") else "aircraft"
        else:
            state["traffic_mode"] = mode if mode in TRAFFIC_MODES else "aircraft"
        migrated = True
    state["ais_enabled"] = state["traffic_mode"] in ("marine", "both")
    if migrated:
        _save(state)
    return state


_state = _load()
try:
    _settings_mtime = os.path.getmtime(SETTINGS_PATH)
except OSError:
    _settings_mtime = None


def _settings_snapshot(state: dict) -> tuple:
    """Comparable tuple of portal-synced settings."""
    return (
        state.get("scale_index"),
        state.get("distance_units"),
        state.get("theme_index"),
        state.get("show_compass_rose"),
        state.get("show_sweep"),
        state.get("min_height_ft"),
        state.get("brightness_percent"),
        state.get("auto_idle_clock"),
        state.get("flight_detail_timeout_s"),
        state.get("clock_timeout_s"),
        state.get("clock_12hr"),
        state.get("auto_timezone"),
        state.get("traffic_mode"),
        state.get("ais_enabled"),
    )


def reload() -> bool:
    """Reload settings from disk if file changed externally."""
    global _state, _settings_mtime
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return False

    incoming = {**_defaults, **data}
    if _settings_snapshot(incoming) == _settings_snapshot(_state):
        try:
            _settings_mtime = os.path.getmtime(SETTINGS_PATH)
        except OSError:
            pass
        return False

    _state = _load()
    try:
        _settings_mtime = os.path.getmtime(SETTINGS_PATH)
    except OSError:
        _settings_mtime = None
    _sync_config_min_height()
    # Re-apply runtime palette when theme changes are saved externally
    # (e.g. from the web portal process).
    apply_theme_colors()
    return True


def _sync_config_min_height():
    try:
        import config
        h = min_height_ft()
        config.MIN_ALTITUDE = h
        config.MIN_HEIGHT = h
    except ImportError:
        pass


def min_height_ft() -> int:
    return _snap_min_height(_state.get("min_height_ft", 1000))


def cycle_min_height():
    opts = MIN_HEIGHT_OPTIONS
    current = min_height_ft()
    idx = opts.index(current) if current in opts else 0
    _state["min_height_ft"] = opts[(idx + 1) % len(opts)]
    _sync_config_min_height()
    _save(_state)


def set_min_height_ft(value: int):
    _state["min_height_ft"] = _snap_min_height(value)
    _sync_config_min_height()
    _save(_state)


_sync_config_min_height()


def brightness_percent():
    return int(_state.get("brightness_percent", 100))


def set_brightness_percent(value: int):
    _state["brightness_percent"] = max(10, min(100, int(value)))
    _save(_state)


def distance_units() -> str:
    units = str(_state.get("distance_units", "km")).lower()
    if units not in ("km", "mi", "nm"):
        return "km"
    return units


def distance_in_miles():
    return distance_units() == "mi"


def toggle_distance_units():
    order = ["km", "mi", "nm"]
    cur = distance_units()
    _state["distance_units"] = order[(order.index(cur) + 1) % len(order)]
    _save(_state)


def set_distance_units(units: str):
    raw = str(units or "").strip().lower()
    if raw not in ("km", "mi", "nm"):
        raw = "km"
    _state["distance_units"] = raw
    _save(_state)


def show_sweep_line() -> bool:
    return bool(_state.get("show_sweep", True))


def toggle_sweep_line():
    _state["show_sweep"] = not show_sweep_line()
    _save(_state)


def set_show_sweep_line(enabled: bool):
    _state["show_sweep"] = bool(enabled)
    _save(_state)


def traffic_mode() -> str:
    """What the radar shows: aircraft, marine (AIS), or both."""
    mode = str(_state.get("traffic_mode") or "").strip().lower()
    if mode in TRAFFIC_MODES:
        return mode
    return "both" if _state.get("ais_enabled") else "aircraft"


def traffic_mode_label() -> str:
    return traffic_mode()


def aircraft_enabled() -> bool:
    return traffic_mode() in ("aircraft", "both")


def ais_enabled() -> bool:
    """True when marine traffic should be fetched / shown."""
    return traffic_mode() in ("marine", "both")


def cycle_traffic_mode():
    order = list(TRAFFIC_MODES)
    cur = traffic_mode()
    nxt = order[(order.index(cur) + 1) % len(order)]
    set_traffic_mode(nxt)


def set_traffic_mode(mode: str):
    raw = str(mode or "").strip().lower()
    if raw not in TRAFFIC_MODES:
        raw = "aircraft"
    _state["traffic_mode"] = raw
    _state["ais_enabled"] = raw in ("marine", "both")
    _save(_state)
    _sync_ais_client()


def toggle_ais_enabled():
    """Legacy: flip between aircraft-only and both (keeps old callers working)."""
    set_traffic_mode("aircraft" if ais_enabled() else "both")


def set_ais_enabled(enabled: bool):
    """Legacy portal/API: on → both, off → aircraft-only."""
    set_traffic_mode("both" if enabled else "aircraft")


def _sync_ais_client():
    try:
        from utilities.ais_client import sync_ais_client

        sync_ais_client()
    except Exception:
        logger.debug("AIS client sync skipped", exc_info=True)


def auto_timezone_enabled() -> bool:
    return bool(_state.get("auto_timezone", True))


def toggle_auto_timezone():
    _state["auto_timezone"] = not auto_timezone_enabled()
    _save(_state)


def show_compass_rose():
    return bool(_state.get("show_compass_rose", True))


def toggle_compass_rose():
    _state["show_compass_rose"] = not _state["show_compass_rose"]
    _save(_state)


def set_show_compass_rose(enabled: bool):
    _state["show_compass_rose"] = bool(enabled)
    _save(_state)


def scale_index():
    from display.round_touch import scale

    try:
        idx = int(_state.get("scale_index", 1))
    except (TypeError, ValueError):
        idx = 1
    return max(0, min(idx, len(scale.SCALE_BANDS) - 1))


def set_scale_index(index: int):
    from display.round_touch import scale

    _state["scale_index"] = max(0, min(int(index), len(scale.SCALE_BANDS) - 1))
    _save(_state)


def cycle_scale():
    from display.round_touch import scale

    set_scale_index((scale_index() + 1) % len(scale.SCALE_BANDS))


def scale_label() -> str:
    from display.round_touch import scale

    return scale.format_band_tag(scale_index(), distance_units())


def theme_index() -> int:
    try:
        idx = int(_state.get("theme_index", color_presets.DEFAULT_THEME_INDEX))
    except (TypeError, ValueError):
        idx = color_presets.DEFAULT_THEME_INDEX
    return max(0, min(idx, color_presets.THEME_COUNT - 1))


def set_theme_index(index: int):
    _state["theme_index"] = max(0, min(int(index), color_presets.THEME_COUNT - 1))
    _save(_state)
    apply_theme_colors()


def use_12hr_clock() -> bool:
    return bool(_state.get("clock_12hr", True))


def toggle_clock_format():
    _state["clock_12hr"] = not use_12hr_clock()
    _save(_state)


def auto_idle_clock_enabled() -> bool:
    return bool(_state.get("auto_idle_clock", True))


def toggle_auto_idle_clock():
    _state["auto_idle_clock"] = not auto_idle_clock_enabled()
    _save(_state)


def set_auto_idle_clock_enabled(enabled: bool):
    _state["auto_idle_clock"] = bool(enabled)
    _save(_state)


def flight_detail_timeout_s() -> int:
    try:
        val = int(_state.get("flight_detail_timeout_s", 20))
    except (TypeError, ValueError):
        val = 20
    if val not in (0, 10, 20, 30):
        val = 20
    return val


def set_flight_detail_timeout_s(value: int):
    try:
        val = int(value)
    except (TypeError, ValueError):
        val = 20
    if val not in (0, 10, 20, 30):
        val = 20
    _state["flight_detail_timeout_s"] = val
    _save(_state)


def clock_timeout_s() -> int:
    try:
        val = int(_state.get("clock_timeout_s", 10))
    except (TypeError, ValueError):
        val = 10
    if val not in (0, 5, 10, 15):
        val = 10
    return val


def set_clock_timeout_s(value: int):
    try:
        val = int(value)
    except (TypeError, ValueError):
        val = 10
    if val not in (0, 5, 10, 15):
        val = 10
    _state["clock_timeout_s"] = val
    _save(_state)


def apply_theme_colors():
    palette = color_presets.THEMES[theme_index()]
    theme.GRID = palette["grid"]
    theme.CROSSHAIR = palette.get("crosshair", palette["grid"])
    theme.SWEEP = palette["sweep"]
    theme.SWEEP_TRAIL = palette["sweep_trail"]
    theme.LABEL = palette["label"]
    # Fixed radar chrome (FlightScnr radar_theme.h).
    theme.BG = (2, 15, 3)
    theme.AIRCRAFT = (255, 180, 40)
    theme.TAG_TYPE = (255, 200, 0)
    theme.TAG_ALT_ASCEND = (0, 255, 255)
    theme.TAG_ALT_DESCEND = (255, 0, 255)


apply_theme_colors()
