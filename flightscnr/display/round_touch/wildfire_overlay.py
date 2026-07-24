"""Wildfire overlay router: CAL FIRE in California, NASA FIRMS elsewhere."""

from __future__ import annotations

import logging
import math
import os
from typing import Any

import pygame

from display.round_touch import calfire_overlay, firms_overlay, geo, theme

logger = logging.getLogger("flightscnr.display")

POLL_TTL_S = min(firms_overlay.POLL_TTL_S, calfire_overlay.POLL_TTL_S)

_ICON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "..",
    "assets",
    "fire_icon.png",
)
_ICON_HEIGHT = 14
_icon_cache: dict[int, pygame.Surface] = {}
_icon_warned = False


def using_calfire() -> bool:
    return calfire_overlay.home_in_california()


def invalidate() -> None:
    firms_overlay.invalidate()
    calfire_overlay.invalidate()


def request_refresh(*, force: bool = False) -> None:
    if using_calfire():
        # Do not hit FIRMS while the radar is centered in California.
        firms_overlay.invalidate()
        calfire_overlay.request_refresh(force=force)
    else:
        calfire_overlay.invalidate()
        firms_overlay.request_refresh(force=force)


def get_fires() -> list[dict[str, Any]]:
    if using_calfire():
        return calfire_overlay.get_fires()
    return firms_overlay.get_fires()


def attribution_text() -> str | None:
    if using_calfire():
        return calfire_overlay.attribution_text()
    return firms_overlay.attribution_text()


def fires_by_distance() -> list[dict[str, Any]]:
    if using_calfire():
        return calfire_overlay.fires_by_distance()

    def key(f: dict[str, Any]) -> float:
        try:
            return geo.local_offset_km(f["lat"], f["lon"])[2]
        except Exception:
            return 1e9

    return sorted(get_fires(), key=key)


def _icon_height(fire: dict[str, Any]) -> int:
    base = max(10, theme.s(_ICON_HEIGHT))
    if fire.get("source") == "calfire":
        acres = fire.get("acres")
        try:
            acres_f = float(acres) if acres is not None else 0.0
        except (TypeError, ValueError):
            acres_f = 0.0
        if acres_f >= 1000:
            return base + theme.s(3)
        if acres_f >= 100:
            return base + theme.s(2)
        return base
    frp = fire.get("frp")
    if frp is None:
        return base
    try:
        frp_f = float(frp)
    except (TypeError, ValueError):
        return base
    if frp_f >= 50:
        return base + theme.s(3)
    if frp_f >= 10:
        return base + theme.s(2)
    return base


def fire_icon(height: int) -> pygame.Surface | None:
    """Load and scale fire_icon.png to the given height (cached)."""
    global _icon_warned
    height = max(8, int(height))
    cached = _icon_cache.get(height)
    if cached is not None:
        return cached
    path = os.path.normpath(_ICON_PATH)
    try:
        image = pygame.image.load(path).convert_alpha()
    except (pygame.error, FileNotFoundError, OSError) as exc:
        if not _icon_warned:
            _icon_warned = True
            logger.warning("Could not load fire icon %s: %s", path, exc)
        return None
    src_w, src_h = image.get_size()
    if src_h <= 0:
        return None
    width = max(6, int(round(src_w * (height / float(src_h)))))
    scaled = pygame.transform.smoothscale(image, (width, height))
    _icon_cache[height] = scaled
    return scaled


def _screen_xy(fire: dict[str, Any]) -> tuple[int, int] | None:
    from display.round_touch import map_bg

    try:
        pos = map_bg.lat_lon_to_basemap_screen(fire["lat"], fire["lon"])
        if pos is None:
            pos = geo.lat_lon_to_screen(fire["lat"], fire["lon"])
        return int(pos[0]), int(pos[1])
    except Exception:
        return None


def draw_fires(
    surface: pygame.Surface, pan_offset: tuple[int, int] | None = None
) -> None:
    """Draw small fire icons inside the visible radar circle."""
    fires = get_fires()
    if not fires:
        return

    ox = int(pan_offset[0]) if pan_offset else 0
    oy = int(pan_offset[1]) if pan_offset else 0
    max_r = theme.VISIBLE_RADIUS - theme.s(2)
    cx, cy = theme.CENTER_X, theme.CENTER_Y
    for fire in fires:
        pos = _screen_xy(fire)
        if pos is None:
            continue
        x, y = pos[0] + ox, pos[1] + oy
        if math.hypot(x - cx, y - cy) > max_r:
            continue
        icon = fire_icon(_icon_height(fire))
        if icon is not None:
            rect = icon.get_rect(center=(int(x), int(y)))
            surface.blit(icon, rect)
        else:
            r = max(2, theme.s(3))
            pygame.draw.circle(surface, (255, 0, 0), (int(x), int(y)), r)


def pick_fire_at(tap_x: int, tap_y: int, alt_x=None, alt_y=None) -> dict[str, Any] | None:
    """Nearest fire under a tap (icon hit radius)."""
    fires = get_fires()
    if not fires:
        return None
    points = [(tap_x, tap_y)]
    if alt_x is not None and alt_y is not None:
        points.append((alt_x, alt_y))
    hit_r = max(theme.TAP_PICK_RADIUS, theme.s(28))
    hit_r2 = hit_r * hit_r
    best = None
    best_d2 = None
    for fire in fires:
        pos = _screen_xy(fire)
        if pos is None:
            continue
        x, y = pos
        for px, py in points:
            d2 = (x - px) ** 2 + (y - py) ** 2
            if d2 <= hit_r2 and (best_d2 is None or d2 < best_d2):
                best = fire
                best_d2 = d2
    return best
