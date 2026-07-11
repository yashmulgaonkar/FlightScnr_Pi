"""Aircraft category icons from adsb-tracker (ICAO type → icon PNG)."""

from __future__ import annotations

import json
import logging
import os

import pygame

logger = logging.getLogger(__name__)

_ASSETS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "..",
    "assets",
    "aircraft",
    "icons",
)
_MAPPING_PATH = os.path.join(_ASSETS_DIR, "aircraft-icons.json")

_FIGHTER_PREFIXES = (
    "F14", "F15", "F16", "F18", "F22", "F35", "F100", "F104", "F111", "F117",
    "EUFI", "RFAL", "HAWK", "TORN", "SU27", "SU30", "SU35", "MIG29", "MIG31",
    "JAS39", "M2K", "M346",
)

# Avoid bare "H" — matches Hawker jets (H25B, etc.). Mapped H125/H145… need no prefix.
_HELICOPTER_PREFIXES = ("EC", "AS", "AW", "R4", "R6", "MI", "KA", "BK", "MD5")

_DEFAULT_CATEGORY = "large-jet-2"

# Helicopter artwork has more padding in the source PNG; boost draw size only.
_CATEGORY_SIZE_SCALE = {
    "helicopter": 1.85,
    "drone": 0.5,
    "military-drone": 0.5,
    "balloon": 0.5,
    "airship": 0.5,
    "glider": 0.5,
}

_type_to_category: dict[str, str] | None = None
_icon_files: dict[str, str] | None = None
_surface_cache: dict[tuple[str, int, tuple], pygame.Surface] = {}
_assets_warned = False


def _load_mapping() -> None:
    global _type_to_category, _icon_files
    if _type_to_category is not None:
        return
    _type_to_category = {}
    _icon_files = {}
    try:
        with open(_MAPPING_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load aircraft icon mapping: %s", exc)
        return

    for category, info in (data.get("icons") or {}).items():
        filename = info.get("file")
        if filename:
            _icon_files[category] = os.path.join(_ASSETS_DIR, filename)

    for category, codes in (data.get("typeCodeMapping") or {}).items():
        if category.startswith("_"):
            continue
        for code in codes:
            key = str(code).upper()
            # Helicopter mapping wins over later duplicate entries (e.g. EC35).
            if key in _type_to_category and _type_to_category[key] == "helicopter":
                continue
            _type_to_category[key] = category


def assets_available() -> bool:
    _load_mapping()
    if not _icon_files:
        return False
    return any(os.path.isfile(path) for path in _icon_files.values())


def _category_for_type(plane_type: str) -> str | None:
    _load_mapping()
    if not _type_to_category:
        return None
    code = "".join((plane_type or "").upper().split())
    if not code:
        return None
    if code in _type_to_category:
        return _type_to_category[code]
    for length in range(len(code), 2, -1):
        prefix = code[:length]
        if prefix in _type_to_category:
            return _type_to_category[prefix]
    for mapped, category in _type_to_category.items():
        if code.startswith(mapped) or mapped.startswith(code):
            return category
    return None


def _is_helicopter_type(plane_type: str) -> bool:
    _load_mapping()
    code = "".join((plane_type or "").upper().split())
    if not code:
        return False
    if code in _type_to_category and _type_to_category[code] == "helicopter":
        return True
    try:
        from utilities.overhead import HELICOPTER_TYPES

        if code in HELICOPTER_TYPES:
            return True
    except ImportError:
        pass
    return any(code.startswith(prefix) for prefix in _HELICOPTER_PREFIXES)


def _military_category(plane_type: str, mapped: str | None) -> str:
    if mapped in ("military-fighter", "military-transport"):
        return mapped
    code = "".join((plane_type or "").upper().split())
    if any(code.startswith(prefix) for prefix in _FIGHTER_PREFIXES):
        return "military-fighter"
    return "military-transport"


def icon_category(flight: dict | None) -> str:
    """Resolve adsb-tracker icon category for a flight dict."""
    flight = flight or {}
    plane_type = flight.get("plane") or ""

    mapped = _category_for_type(plane_type)
    # Explicit ICAO mapping wins (e.g. BALL → balloon, TEX2 → small-prop-single)
    # over helicopter / military heuristics.
    if mapped:
        return mapped

    if _is_helicopter_type(plane_type):
        return "helicopter"

    try:
        from utilities import aircraft_alert

        if aircraft_alert.is_military(flight):
            return _military_category(plane_type, None)
    except ImportError:
        pass

    return _DEFAULT_CATEGORY


def _icon_path(category: str) -> str | None:
    _load_mapping()
    if not _icon_files:
        return None
    path = _icon_files.get(category) or _icon_files.get(_DEFAULT_CATEGORY)
    if path and os.path.isfile(path):
        return path
    return None


def _colorize(icon: pygame.Surface, color: tuple) -> pygame.Surface:
    """Recolor a black silhouette icon to the radar theme color."""
    tinted = pygame.Surface(icon.get_size(), pygame.SRCALPHA)
    r, g, b = color[:3]
    for x in range(icon.get_width()):
        for y in range(icon.get_height()):
            _, _, _, alpha = icon.get_at((x, y))
            if alpha:
                tinted.set_at((x, y), (r, g, b, alpha))
    return tinted


def get_icon_surface(category: str, size: int, color: tuple) -> pygame.Surface | None:
    """Load, scale, and tint an icon (nose points up / north)."""
    scale = _CATEGORY_SIZE_SCALE.get(category, 1.0)
    side = max(12, int(round(size * scale)))
    key = (category, side, color[:3])
    cached = _surface_cache.get(key)
    if cached is not None:
        return cached

    path = _icon_path(category)
    if not path:
        return None
    try:
        image = pygame.image.load(path).convert_alpha()
    except pygame.error as exc:
        logger.warning("Could not load aircraft icon %s: %s", path, exc)
        return None

    if image.get_width() != side or image.get_height() != side:
        image = pygame.transform.smoothscale(image, (side, side))

    tinted = _colorize(image, color)
    _surface_cache[key] = tinted
    return tinted


def draw_icon(
    surface: pygame.Surface,
    flight: dict | None,
    center: tuple[int, int],
    heading_deg: float,
    color: tuple,
    *,
    size: int,
) -> bool:
    """Draw a categorized aircraft icon. Returns True if a PNG icon was drawn."""
    global _assets_warned
    category = icon_category(flight)
    icon = get_icon_surface(category, size, color)
    if icon is None:
        if not _assets_warned and not assets_available():
            _assets_warned = True
            logger.warning(
                "Aircraft icons not found in %s — run install-pi.sh to download them",
                _ASSETS_DIR,
            )
        return False

    rotated = pygame.transform.rotate(icon, -float(heading_deg))
    rect = rotated.get_rect(center=center)
    surface.blit(rotated, rect)
    return True
