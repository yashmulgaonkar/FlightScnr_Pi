"""Persisted UI settings for round touch display."""

import json
import logging
import os

from display.round_touch import color_presets, theme

logger = logging.getLogger("flightscnr.display")

DATA_DIR = os.environ.get("FLIGHTSCNR_DATA_DIR", "/var/lib/flightscnr")
SETTINGS_PATH = os.path.join(DATA_DIR, "round_touch_settings.json")

MIN_HEIGHT_OPTIONS = (500, 1000, 1500)

_defaults = {
    "brightness_percent": 100,
    "distance_miles": False,
    "show_compass_rose": True,
    "scale_index": 1,
    "theme_index": color_presets.DEFAULT_THEME_INDEX,
    "clock_12hr": True,
    "min_height_ft": 1000,
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

        state["distance_miles"] = DISTANCE_UNITS.strip().lower() == "imperial"
        state["scale_index"] = scale.index_for_radius_nm(SEARCH_RADIUS_NM)
        state["min_height_ft"] = _snap_min_height(MIN_HEIGHT)
    except ImportError:
        pass


def _save(data):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp_path = SETTINGS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, SETTINGS_PATH)
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
    if "tracked_stats_mode" in state:
        del state["tracked_stats_mode"]
        migrated = True
    if migrated:
        _save(state)
    return state


_state = _load()


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


_sync_config_min_height()


def brightness_percent():
    return int(_state.get("brightness_percent", 100))


def set_brightness_percent(value: int):
    _state["brightness_percent"] = max(10, min(100, int(value)))
    _save(_state)


def distance_in_miles():
    return bool(_state.get("distance_miles", False))


def toggle_distance_units():
    _state["distance_miles"] = not _state["distance_miles"]
    _save(_state)


def show_compass_rose():
    return bool(_state.get("show_compass_rose", True))


def toggle_compass_rose():
    _state["show_compass_rose"] = not _state["show_compass_rose"]
    _save(_state)


def scale_index():
    return int(_state.get("scale_index", 1))


def set_scale_index(index: int):
    _state["scale_index"] = index
    _save(_state)


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
