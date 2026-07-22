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

# Size relative to the base draw size after alpha-crop (1.0 = theme size).
_CATEGORY_SIZE_SCALE = {
    "large-jet-4": 0.75,
    "large-jet-2": 0.75,
    "medium-jet": 0.75,
    "regional-jet": 0.5,
    "business-jet": 0.5,
    "turboprop": 0.75,
    "small-prop-single": 0.5,
    "small-prop-twin": 0.75,
    "cargo": 0.75,
    "helicopter": 0.75,
    "military-helicopter": 0.75,
    "military-fighter": 0.75,
    "military-transport": 0.75,
    "fighter": 0.75,
    "drone": 0.5,
    "military-drone": 0.5,
    "balloon": 0.5,
    "airship": 0.75,
    "glider": 0.75,
    "unknown": 1.0,
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
            # Prefer military-* / first explicit mapping; don't clobber a military
            # category with a later civilian duplicate.
            if key in _type_to_category:
                existing = _type_to_category[key]
                if existing.startswith("military-"):
                    continue
                if category.startswith("military-"):
                    _type_to_category[key] = category
                    continue
                if existing in ("helicopter", "military-helicopter"):
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
    if code in _type_to_category and _type_to_category[code] in (
        "helicopter",
        "military-helicopter",
    ):
        return True
    try:
        from utilities.overhead import HELICOPTER_TYPES

        if code in HELICOPTER_TYPES:
            return True
    except ImportError:
        pass
    return any(code.startswith(prefix) for prefix in _HELICOPTER_PREFIXES)


def _military_category(plane_type: str, mapped: str | None) -> str:
    if mapped in ("military-fighter", "military-transport", "military-helicopter", "military-drone"):
        return mapped
    if _is_helicopter_type(plane_type):
        return "military-helicopter"
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


def _crop_to_alpha(image: pygame.Surface, *, pad: int = 2) -> pygame.Surface:
    """Trim empty transparent padding so the silhouette fills the draw size."""
    mask = pygame.mask.from_surface(image)
    rects = mask.get_bounding_rects()
    if not rects:
        return image

    # Artwork can be disconnected (fuselage + wings); use the union bounds.
    left = min(r.left for r in rects)
    top = min(r.top for r in rects)
    right = max(r.right for r in rects)
    bottom = max(r.bottom for r in rects)
    w, h = image.get_size()
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(w, right + pad)
    bottom = min(h, bottom + pad)
    if right <= left or bottom <= top:
        return image
    if left == 0 and top == 0 and right == w and bottom == h:
        return image
    return image.subsurface((left, top, right - left, bottom - top)).copy()


def _fit_to_side(image: pygame.Surface, side: int) -> pygame.Surface:
    """Scale preserving aspect ratio; center on a transparent side×side canvas."""
    w, h = image.get_size()
    if w <= 0 or h <= 0:
        return pygame.Surface((side, side), pygame.SRCALPHA)

    scale = side / max(w, h)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    if new_w != w or new_h != h:
        image = pygame.transform.smoothscale(image, (new_w, new_h))

    if new_w == side and new_h == side:
        return image

    canvas = pygame.Surface((side, side), pygame.SRCALPHA)
    canvas.blit(image, ((side - new_w) // 2, (side - new_h) // 2))
    return canvas


def _colorize(icon: pygame.Surface, color: tuple) -> pygame.Surface:
    """Recolor a black silhouette icon to the radar theme color (keep PNG alpha)."""
    tinted = pygame.Surface(icon.get_size(), pygame.SRCALPHA)
    tinted.fill((*color[:3], 255))
    src_a = pygame.surfarray.pixels_alpha(icon)
    dst_a = pygame.surfarray.pixels_alpha(tinted)
    dst_a[:] = src_a
    del src_a, dst_a
    return tinted


def get_icon_surface(category: str, size: int, color: tuple) -> pygame.Surface | None:
    """Load, crop padding, scale, and tint an icon (nose points up / north)."""
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

    image = _crop_to_alpha(image)
    image = _fit_to_side(image, side)
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
