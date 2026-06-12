"""Footer button PNG loading for round touch display."""

from __future__ import annotations

import os

import pygame

try:
    from PIL import Image
except ImportError:
    Image = None

_cache: dict[tuple[str, bool, int, int], pygame.Surface | None] = {}


def _package_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def buttons_dir() -> str:
    try:
        from config import BUTTONS_DIR

        custom = (BUTTONS_DIR or "").strip()
        if custom and os.path.isdir(custom):
            return custom
    except ImportError:
        pass

    base = _package_root()
    ref = os.path.join(base, "buttons")
    if os.path.isdir(ref):
        return ref
    if os.path.isfile(ref):
        try:
            with open(ref, encoding="utf-8") as fh:
                rel = fh.read().strip()
            candidate = os.path.normpath(os.path.join(base, rel))
            if os.path.isdir(candidate):
                return candidate
        except OSError:
            pass
    return ref


# Alternate filenames (without .png) checked after the primary name.
_BUTTON_ALIASES: dict[str, tuple[str, ...]] = {
    "radar": ("radar_icon",),
}

# Wide bar artwork — scale to the full footer slot, not a square icon.
_FULL_SLOT_BUTTONS = frozenset({"prev", "next"})


def _button_path(kind: str, *, active: bool) -> str | None:
    code = (kind or "").strip().lower()
    if not code:
        return None
    root = buttons_dir()
    names: list[str] = []
    if active:
        names.append(f"{code}_active")
    names.append(code)
    for alias in _BUTTON_ALIASES.get(code, ()):
        if active:
            names.append(f"{alias}_active")
        names.append(alias)
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        path = os.path.join(root, f"{name}.png")
        if os.path.isfile(path):
            return path
    return None


def _trim_visible(image: "Image.Image", *, lum_min: int = 35, alpha_min: int = 32) -> "Image.Image":
    """Crop to artwork that reads on the dark display background."""
    rgba = image.convert("RGBA")
    w, h = rgba.size
    mask = Image.new("L", (w, h), 0)
    src = rgba.load()
    dst = mask.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = src[x, y]
            if a > alpha_min:
                lum = int(0.299 * r + 0.587 * g + 0.114 * b)
                if lum > lum_min:
                    dst[x, y] = 255
    bbox = mask.getbbox()
    if bbox:
        return rgba.crop(bbox)
    return rgba


def _scale_to_fit(src_w: int, src_h: int, max_w: int, max_h: int) -> tuple[int, int]:
    if src_w <= 0 or src_h <= 0 or max_w <= 0 or max_h <= 0:
        return 0, 0
    scale = min(max_w / src_w, max_h / src_h)
    return max(1, int(round(src_w * scale))), max(1, int(round(src_h * scale)))


def _pin_rendered_size(width: int, height: int) -> tuple[int, int] | None:
    """On-screen pixel size of the pin icon in the same footer slot."""
    path = _button_path("pin", active=False) or _button_path("pin", active=True)
    if not path:
        return None
    try:
        image = _trim_visible(Image.open(path))
    except OSError:
        return None
    return _scale_to_fit(image.size[0], image.size[1], width, height)


def _icon_target_size(
    kind: str,
    src_w: int,
    src_h: int,
    width: int,
    height: int,
) -> tuple[int, int]:
    base_w, base_h = _scale_to_fit(src_w, src_h, width, height)
    if kind.lower() != "radar":
        return base_w, base_h

    # Wide footer bars (flight detail) keep radar within the slot; square slots may boost.
    square = abs(width - height) <= max(2, height // 8)
    if not square:
        return base_w, base_h

    pin_size = _pin_rendered_size(width, height)
    if not pin_size:
        return base_w, base_h

    # Radar artwork includes a thick outer ring; scale up to match pin disc size.
    boost = 1.3
    return (
        max(1, int(round(pin_size[0] * boost))),
        max(1, int(round(pin_size[1] * boost))),
    )


def button_draw_size(kind: str, width: int, height: int) -> tuple[int, int]:
    """Return target draw size for a footer button kind."""
    if kind.lower() in _FULL_SLOT_BUTTONS:
        return width, height
    dim = min(width, height)
    return dim, dim


def load_button_surface(
    kind: str,
    width: int,
    height: int,
    *,
    active: bool = False,
) -> pygame.Surface | None:
    """Load a footer button PNG scaled to fit the tap target."""
    if width <= 0 or height <= 0 or Image is None:
        return None

    key = (kind.lower(), active, width, height)
    if key in _cache:
        return _cache[key]

    surface = None
    path = _button_path(kind, active=active)
    if path:
        try:
            image = Image.open(path).convert("RGBA")
            if kind.lower() not in _FULL_SLOT_BUTTONS:
                image = _trim_visible(image)
            src_w, src_h = image.size
            target_w, target_h = _icon_target_size(kind, src_w, src_h, width, height)
            new_w, new_h = _scale_to_fit(src_w, src_h, target_w, target_h)
            if new_w > 0 and new_h > 0:
                try:
                    resample = Image.Resampling.LANCZOS
                except AttributeError:
                    resample = Image.LANCZOS
                image = image.resize((new_w, new_h), resample)
                surface = pygame.image.frombuffer(
                    image.tobytes(), image.size, "RGBA"
                ).convert_alpha()
        except OSError:
            surface = None

    _cache[key] = surface
    return surface
