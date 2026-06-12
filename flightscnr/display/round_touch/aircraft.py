"""Top-down aircraft icon (FlightScnr aircraft_symbol)."""

import math
import pygame

from display.round_touch import theme

# Right-side outline (nose toward -Y). Chunky flat icon: thick fuselage,
# swept wings with blunt rounded tips, small tail stabs, no vertical fin.
_SILHOUETTE_HALF = (
    (0.0, -13.0),   # nose tip
    (1.4, -12.7),
    (2.4, -12.0),   # rounded nose
    (2.7, -10.5),
    (2.7, -6.0),    # fuselage fore
    (2.7, -4.5),    # wing root (leading edge)
    (12.0, -0.8),   # wing LE (moderate sweep, straight edge)
    (12.4, 0.6),    # blunt rounded wingtip
    (11.8, 2.2),    # wing trailing edge
    (2.7, 3.4),     # wing root TE
    (2.7, 8.5),     # fuselage aft
    (2.6, 10.2),    # tail stab root
    (6.2, 11.4),    # stab LE tip
    (6.0, 12.2),    # rounded stab tip
    (5.4, 12.6),    # stab TE
    (2.4, 12.8),    # rounded tail
)


def _rotate(x, y, heading_deg):
    rad = math.radians(heading_deg)
    sin_h = math.sin(rad)
    cos_h = math.cos(rad)
    rx = x * cos_h - y * sin_h
    ry = x * sin_h + y * cos_h
    return rx, ry


def _map_local(lx, ly, cx, cy, heading_deg):
    rx, ry = _rotate(lx, ly, heading_deg)
    return int(round(cx + rx)), int(round(cy + ry))


def _silhouette_outline(scale: float) -> list[tuple[float, float]]:
    half = [(x * scale, y * scale) for x, y in _SILHOUETTE_HALF]
    outline = list(half)
    outline.append((0.0, 13.0 * scale))
    for x, y in reversed(half[1:]):
        outline.append((-x, y))
    return outline


def _draw_silhouette(surface, cx, cy, heading_deg, color, scale: float):
    pts = [_map_local(x, y, cx, cy, heading_deg) for x, y in _silhouette_outline(scale)]
    if len(pts) >= 3:
        pygame.draw.polygon(surface, color, pts)


def draw_plane_icon(surface, cx, cy, heading_deg, color, compact=False):
    """Filled chunky top-down jet icon."""
    scale = 0.40 if compact else 0.68
    _draw_silhouette(surface, cx, cy, heading_deg, color, scale)


def draw_progress_plane(surface, cx, cy, color):
    """Progress-bar marker — same icon, nose points right."""
    scale = max(0.65, theme.s(0.58))
    _draw_silhouette(surface, cx, cy, 90, color, scale)


def format_altitude(alt_ft) -> str:
    if alt_ft is None:
        return "—"
    try:
        alt = int(alt_ft)
    except (TypeError, ValueError):
        return "—"
    if alt <= 0:
        return "—"
    if alt >= 18000:
        return f"FL{round(alt / 100)}"
    return f"{alt:,}ft"


def altitude_tag_color(vertical_speed):
    if vertical_speed is not None and vertical_speed < -64:
        return theme.TAG_ALT_DESCEND
    return theme.TAG_ALT_ASCEND
