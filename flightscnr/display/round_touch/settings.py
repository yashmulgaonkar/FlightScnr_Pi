"""Persisted UI settings for round touch display."""

import json
import logging
import os

from display.round_touch import color_presets, theme
logger = logging.getLogger("flightscnr.display")

DATA_DIR = os.environ.get("FLIGHTSCNR_DATA_DIR", "/var/lib/flightscnr")
SETTINGS_PATH = os.path.join(DATA_DIR, "round_touch_settings.json")
RELOAD_REQUEST_PATH = os.path.join(DATA_DIR, "round_touch_settings.reload")
_settings_mtime: float | None = None
# True when _state matches disk. Slider drags set this False until persist.
_disk_synced = True

MIN_HEIGHT_OPTIONS = (0, 500, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000)
# Portal / persistence presets (fine steps).
MAX_HEIGHT_OPTIONS = (
    1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000,
    6000, 7000, 8000, 9000, 10000, 12000, 15000, 16000, 18000,
    20000, 25000, 30000, 35000, 40000, 45000, 50000, 100000,
)
# Shorter list for on-device Options cycling (must be ⊆ MAX_HEIGHT_OPTIONS).
MAX_HEIGHT_CYCLE_OPTIONS = (
    3000, 5000, 10000, 15000, 20000, 30000, 45000, 100000,
)
TRAFFIC_MODES = ("aircraft", "marine", "both")
MAP_STYLES = ("dark", "light", "vfr")

# Waveshare DSI panels stay lit near ~3% (raw ~8/255); 10% was needlessly bright at night.
BRIGHTNESS_MIN_PERCENT = 3
BRIGHTNESS_MAX_PERCENT = 100
# VFR chart opacity on the radar (lower = more washed / pale).
VFR_OPACITY_MIN_PERCENT = 15
VFR_OPACITY_MAX_PERCENT = 100


def clamp_brightness_percent(value: int) -> int:
    return max(BRIGHTNESS_MIN_PERCENT, min(BRIGHTNESS_MAX_PERCENT, int(value)))


def clamp_vfr_opacity_percent(value: int) -> int:
    return max(VFR_OPACITY_MIN_PERCENT, min(VFR_OPACITY_MAX_PERCENT, int(value)))

_defaults = {
    "brightness_percent": 100,
    "distance_units": "km",
    "show_compass_rose": True,
    "show_range_rings": True,
    "show_aircraft_tag": True,
    # Real-world direction at the top of the screen (0=north-up).
    "facing_deg": 0.0,
    "show_sweep": True,
    "show_precipitation": True,
    "show_wildfires": False,
    "scale_index": 1,
    "theme_index": color_presets.DEFAULT_THEME_INDEX,
    "theme_custom": False,
    "custom_theme_rgb": list(color_presets.DEFAULT_CUSTOM_RGB),
    "theme_palette_v": color_presets.THEME_PALETTE_V,
    "clock_12hr": True,
    "auto_timezone": True,
    "min_height_ft": 1000,
    "max_height_ft": 100000,
    "auto_idle_clock": True,
    "flight_detail_timeout_s": 20,
    "clock_timeout_s": 10,
    # aircraft | marine | both — what the radar shows
    "traffic_mode": "aircraft",
    # Kept in sync with traffic_mode for older readers / portal payloads
    "ais_enabled": False,
    # dark | light | vfr — radar basemap (see map_bg)
    "map_style": "dark",
    "vfr_map_opacity": 45,
    # Clockwise UI + touch mapping: 0, 90, 180, 270 (physical panel mount).
    "display_rotation": 90,
}

# Live preview while calibrating facing (not persisted until save).
_facing_preview: float | None = None


def _normalize_facing(deg) -> float:
    try:
        value = float(deg)
    except (TypeError, ValueError):
        return 0.0
    if not (value == value):  # NaN
        return 0.0
    return value % 360.0


def _normalize_display_rotation(deg) -> int:
    try:
        value = int(deg) % 360
    except (TypeError, ValueError):
        return 90
    if value not in (0, 90, 180, 270):
        value = round(value / 90) * 90 % 360
    return value


def _env_display_rotation() -> int:
    try:
        from config import DISPLAY_ROTATION

        return _normalize_display_rotation(DISPLAY_ROTATION)
    except ImportError:
        return _normalize_display_rotation(os.environ.get("DISPLAY_ROTATION", "90"))


def _snap_min_height(value) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 1000
    if v in MIN_HEIGHT_OPTIONS:
        return v
    return min(MIN_HEIGHT_OPTIONS, key=lambda opt: abs(opt - v))


def _snap_max_height(value) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 100000
    if v in MAX_HEIGHT_OPTIONS:
        return v
    return min(MAX_HEIGHT_OPTIONS, key=lambda opt: abs(opt - v))


def _env_min_height() -> int:
    try:
        from config import MIN_HEIGHT
        return _snap_min_height(MIN_HEIGHT)
    except ImportError:
        return 1000


def _env_max_height() -> int:
    try:
        from config import MAX_HEIGHT
        return _snap_max_height(MAX_HEIGHT)
    except ImportError:
        return 100000


def _ensure_height_band(state: dict | None = None) -> bool:
    """Keep max_height_ft >= min_height_ft. Returns True if state changed."""
    target = state if state is not None else _state
    mn = _snap_min_height(target.get("min_height_ft", 1000))
    mx = _snap_max_height(target.get("max_height_ft", 100000))
    changed = False
    if mn != target.get("min_height_ft"):
        target["min_height_ft"] = mn
        changed = True
    if mx < mn:
        bumped = next((opt for opt in MAX_HEIGHT_OPTIONS if opt >= mn), MAX_HEIGHT_OPTIONS[-1])
        if bumped != mx:
            mx = bumped
            changed = True
    if mx != target.get("max_height_ft"):
        target["max_height_ft"] = mx
        changed = True
    return changed


def _seed_from_env(state: dict) -> None:
    """First-run defaults from /etc/flightscnr.env (not applied after settings are saved)."""
    try:
        from config import DISTANCE_UNITS, MIN_HEIGHT, MAX_HEIGHT, SEARCH_RADIUS_NM
        from display.round_touch import map_bg, scale

        state["distance_units"] = "mi" if DISTANCE_UNITS.strip().lower() == "imperial" else "km"
        state["scale_index"] = scale.index_for_radius_nm(SEARCH_RADIUS_NM)
        state["min_height_ft"] = _snap_min_height(MIN_HEIGHT)
        state["max_height_ft"] = _snap_max_height(MAX_HEIGHT)
        _ensure_height_band(state)
        env_style = map_bg.normalize_map_style(os.environ.get("RADAR_MAP_PROVIDER", "dark"))
        # UI only cycles dark/light/vfr; map legacy osm to dark for first-run seed.
        state["map_style"] = env_style if env_style in MAP_STYLES else "dark"
        state["display_rotation"] = _env_display_rotation()
        state["show_wildfires"] = _default_show_wildfires()
    except ImportError:
        state["display_rotation"] = _env_display_rotation()
        state["show_wildfires"] = _default_show_wildfires()


def _default_show_wildfires() -> bool:
    # CalFire (CA) and NIFC WFIGS (elsewhere) need no API key.
    return True


def _save(data):
    global _settings_mtime, _disk_synced
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
        _disk_synced = True
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
    if "max_height_ft" not in data:
        state["max_height_ft"] = _env_max_height()
        migrated = True
    else:
        state["max_height_ft"] = _snap_max_height(state["max_height_ft"])
    if _ensure_height_band(state):
        migrated = True
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
    state["facing_deg"] = _normalize_facing(state.get("facing_deg", 0))
    if "map_style" not in data:
        try:
            from display.round_touch import map_bg

            env_style = map_bg.normalize_map_style(os.environ.get("RADAR_MAP_PROVIDER", "dark"))
            state["map_style"] = env_style if env_style in MAP_STYLES else "dark"
        except ImportError:
            state["map_style"] = "dark"
        migrated = True
    else:
        raw = str(state.get("map_style") or "dark").strip().lower()
        state["map_style"] = raw if raw in MAP_STYLES else "dark"
    try:
        if "vfr_map_opacity" not in data:
            state["vfr_map_opacity"] = 45
            migrated = True
        else:
            state["vfr_map_opacity"] = clamp_vfr_opacity_percent(
                int(state.get("vfr_map_opacity", 45))
            )
    except (TypeError, ValueError):
        state["vfr_map_opacity"] = 45
        migrated = True
    if "display_rotation" not in data:
        state["display_rotation"] = _env_display_rotation()
        migrated = True
    else:
        state["display_rotation"] = _normalize_display_rotation(
            state.get("display_rotation", 90)
        )
    if "show_wildfires" not in data:
        state["show_wildfires"] = _default_show_wildfires()
        migrated = True
    else:
        state["show_wildfires"] = bool(state.get("show_wildfires"))
    if color_presets.migrate_theme_index(state):
        migrated = True
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
        state.get("theme_custom"),
        tuple(color_presets.normalize_rgb(state.get("custom_theme_rgb"))),
        state.get("show_compass_rose"),
        state.get("show_range_rings"),
        state.get("show_aircraft_tag"),
        _normalize_facing(state.get("facing_deg", 0)),
        state.get("show_sweep"),
        state.get("show_precipitation"),
        state.get("show_wildfires"),
        state.get("min_height_ft"),
        state.get("max_height_ft"),
        state.get("brightness_percent"),
        state.get("auto_idle_clock"),
        state.get("flight_detail_timeout_s"),
        state.get("clock_timeout_s"),
        state.get("clock_12hr"),
        state.get("auto_timezone"),
        state.get("traffic_mode"),
        state.get("ais_enabled"),
        state.get("map_style"),
        state.get("vfr_map_opacity"),
        _normalize_display_rotation(state.get("display_rotation", 90)),
    )


def request_reload() -> None:
    """Ask the display process to re-apply settings from disk (cross-process)."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(RELOAD_REQUEST_PATH, "w", encoding="utf-8") as fh:
            fh.write("1\n")
        try:
            os.chmod(RELOAD_REQUEST_PATH, 0o666)
        except OSError:
            pass
        # Bump settings mtime too so pollers that only watch the json notice.
        if os.path.isfile(SETTINGS_PATH):
            os.utime(SETTINGS_PATH, None)
    except OSError as exc:
        logger.warning("Could not request settings reload: %s", exc)


def _consume_reload_request() -> bool:
    if not os.path.isfile(RELOAD_REQUEST_PATH):
        return False
    try:
        os.unlink(RELOAD_REQUEST_PATH)
    except OSError:
        return False
    return True


def reload() -> bool:
    """Reload settings from disk if file changed externally."""
    global _state, _settings_mtime, _disk_synced
    force = _consume_reload_request()
    # Do not clobber in-memory slider edits (brightness / VFR opacity / theme RGB)
    # that have not been flushed to disk yet — otherwise values flicker every poll.
    if not force and not _disk_synced:
        return False
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return force

    incoming = {**_defaults, **data}
    if not force and _settings_snapshot(incoming) == _settings_snapshot(_state):
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
    _disk_synced = True
    _sync_config_min_height()
    _sync_config_max_height()
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


def _sync_config_max_height():
    try:
        import config
        h = max_height_ft()
        config.MAX_ALTITUDE_FT = h
        config.MAX_HEIGHT = h
    except ImportError:
        pass


def min_height_ft() -> int:
    return _snap_min_height(_state.get("min_height_ft", 1000))


def max_height_ft() -> int:
    return _snap_max_height(_state.get("max_height_ft", 100000))


def cycle_min_height():
    opts = MIN_HEIGHT_OPTIONS
    current = min_height_ft()
    idx = opts.index(current) if current in opts else 0
    _state["min_height_ft"] = opts[(idx + 1) % len(opts)]
    _ensure_height_band()
    _sync_config_min_height()
    _sync_config_max_height()
    _save(_state)


def set_min_height_ft(value: int):
    _state["min_height_ft"] = _snap_min_height(value)
    _ensure_height_band()
    _sync_config_min_height()
    _sync_config_max_height()
    _save(_state)


def cycle_max_height():
    opts = MAX_HEIGHT_CYCLE_OPTIONS
    current = max_height_ft()
    greater = [opt for opt in opts if opt > current]
    nxt = greater[0] if greater else opts[0]
    mn = min_height_ft()
    if nxt < mn:
        nxt = next((opt for opt in opts if opt >= mn), opts[-1])
    _state["max_height_ft"] = nxt
    _ensure_height_band()
    _sync_config_max_height()
    _save(_state)


def set_max_height_ft(value: int):
    _state["max_height_ft"] = _snap_max_height(value)
    _ensure_height_band()
    _sync_config_min_height()
    _sync_config_max_height()
    _save(_state)


_sync_config_min_height()
_sync_config_max_height()


def brightness_percent():
    return int(_state.get("brightness_percent", 100))


def set_brightness_percent(value: int, *, persist: bool = True):
    global _disk_synced
    _state["brightness_percent"] = clamp_brightness_percent(value)
    if persist:
        _save(_state)
    else:
        _disk_synced = False


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


def show_precipitation() -> bool:
    return bool(_state.get("show_precipitation", True))


def toggle_show_precipitation():
    _state["show_precipitation"] = not show_precipitation()
    _save(_state)


def set_show_precipitation(enabled: bool):
    _state["show_precipitation"] = bool(enabled)
    _save(_state)


def show_wildfires() -> bool:
    return bool(_state.get("show_wildfires", False))


def toggle_show_wildfires():
    _state["show_wildfires"] = not show_wildfires()
    _save(_state)


def set_show_wildfires(enabled: bool):
    _state["show_wildfires"] = bool(enabled)
    _save(_state)


def map_style() -> str:
    raw = str(_state.get("map_style") or "dark").strip().lower()
    return raw if raw in MAP_STYLES else "dark"


def map_style_label() -> str:
    labels = {"dark": "dark", "light": "light", "vfr": "VFR"}
    return labels.get(map_style(), "dark")


def set_map_style(value: str) -> str:
    from display.round_touch import map_bg

    try:
        style = map_bg.normalize_map_style(value)
    except Exception:
        style = str(value or "dark").strip().lower()
    if style not in MAP_STYLES:
        style = "dark"
    if style == map_style():
        return style
    _state["map_style"] = style
    _save(_state)
    try:
        map_bg.invalidate()
        map_bg.prewarm_all_scales()
    except Exception:
        logger.debug("Map style invalidate/prewarm failed", exc_info=True)
    return style


def cycle_map_style() -> str:
    cur = map_style()
    idx = MAP_STYLES.index(cur) if cur in MAP_STYLES else 0
    return set_map_style(MAP_STYLES[(idx + 1) % len(MAP_STYLES)])


def vfr_map_opacity() -> int:
    try:
        return clamp_vfr_opacity_percent(int(_state.get("vfr_map_opacity", 45)))
    except (TypeError, ValueError):
        return 45


def set_vfr_map_opacity(value: int, *, persist: bool = True) -> int:
    """Set VFR chart opacity (draw-time blend — does not rebuild map tiles)."""
    global _disk_synced
    pct = clamp_vfr_opacity_percent(value)
    _state["vfr_map_opacity"] = pct
    if persist:
        _save(_state)
    else:
        _disk_synced = False
    # Drop draw-time opacity cache so the next frame picks up the new value.
    try:
        from display.round_touch import map_bg

        map_bg.clear_vfr_opacity_blit_cache()
    except Exception:
        pass
    return pct


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


def show_range_rings() -> bool:
    return bool(_state.get("show_range_rings", True))


def toggle_range_rings():
    _state["show_range_rings"] = not show_range_rings()
    _save(_state)


def set_show_range_rings(enabled: bool):
    _state["show_range_rings"] = bool(enabled)
    _save(_state)


def show_aircraft_tag() -> bool:
    return bool(_state.get("show_aircraft_tag", True))


def toggle_show_aircraft_tag():
    _state["show_aircraft_tag"] = not show_aircraft_tag()
    _save(_state)


def set_show_aircraft_tag(enabled: bool):
    _state["show_aircraft_tag"] = bool(enabled)
    _save(_state)


def facing_deg() -> float:
    return _normalize_facing(_state.get("facing_deg", 0))


def set_facing_deg(deg) -> float:
    value = _normalize_facing(deg)
    _state["facing_deg"] = value
    _save(_state)
    return value


def set_facing_preview(deg: float | None) -> None:
    """Set/clear live facing preview used while calibrating (not persisted)."""
    global _facing_preview
    _facing_preview = None if deg is None else _normalize_facing(deg)


def facing_preview() -> float | None:
    return _facing_preview


def effective_facing_deg() -> float:
    if _facing_preview is not None:
        return _facing_preview
    return facing_deg()


def facing_label(deg=None) -> str:
    """Short label for settings: N/E/S/W or integer degrees."""
    value = facing_deg() if deg is None else _normalize_facing(deg)
    names = {0.0: "N", 90.0: "E", 180.0: "S", 270.0: "W"}
    rounded = round(value) % 360
    if float(rounded) in names and abs(value - float(rounded)) < 0.5:
        return names[float(rounded)]
    # Handle 359.7 → 0 for label purposes
    if abs(value) < 0.5 or abs(value - 360.0) < 0.5:
        return "N"
    return f"{int(round(value)) % 360}°"


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


def display_rotation() -> int:
    return _normalize_display_rotation(_state.get("display_rotation", 90))


def set_display_rotation(degrees) -> int:
    value = _normalize_display_rotation(degrees)
    _state["display_rotation"] = value
    _save(_state)
    return value


def cycle_display_rotation() -> int:
    options = (0, 90, 180, 270)
    current = display_rotation()
    try:
        idx = options.index(current)
    except ValueError:
        idx = 0
    return set_display_rotation(options[(idx + 1) % len(options)])


def theme_index() -> int:
    try:
        idx = int(_state.get("theme_index", color_presets.DEFAULT_THEME_INDEX))
    except (TypeError, ValueError):
        idx = color_presets.DEFAULT_THEME_INDEX
    return max(0, min(idx, color_presets.THEME_COUNT - 1))


def theme_custom() -> bool:
    return bool(_state.get("theme_custom", False))


def custom_theme_rgb() -> tuple[int, int, int]:
    return color_presets.normalize_rgb(_state.get("custom_theme_rgb"))


def theme_rgb() -> tuple[int, int, int]:
    """RGB shown on Theme sliders (custom accent or active preset sweep)."""
    if theme_custom():
        return custom_theme_rgb()
    return color_presets.THEMES[theme_index()]["sweep"]


def set_theme_index(index: int):
    idx = max(0, min(int(index), color_presets.THEME_COUNT - 1))
    _state["theme_index"] = idx
    _state["theme_custom"] = False
    _state["custom_theme_rgb"] = list(color_presets.THEMES[idx]["sweep"])
    _save(_state)
    apply_theme_colors()


def set_custom_theme_rgb(r: int, g: int, b: int, *, persist: bool = True):
    global _disk_synced
    _state["theme_custom"] = True
    _state["custom_theme_rgb"] = list(color_presets.normalize_rgb((r, g, b)))
    if persist:
        _save(_state)
    else:
        _disk_synced = False
    apply_theme_colors()


def persist_theme_settings():
    """Flush in-memory theme edits (used after RGB slider release)."""
    _save(_state)


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
    if theme_custom():
        palette = color_presets.palette_from_rgb(*custom_theme_rgb())
    else:
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
