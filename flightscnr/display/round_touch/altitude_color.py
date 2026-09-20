"""Altitude-based aircraft icon coloring, matching the gradient used by
ADS-B Exchange / tar1090 (see wiedehopf/tar1090's ColorByAlt in defaults.js).

The scheme is HSL-based: altitude maps to hue via a set of breakpoints
(linearly interpolated between them), and hue in turn maps to a lightness
value via a second table, so the gradient stays visually balanced rather
than getting muddy at certain hues. Saturation is constant.

Roughly: low altitude is orange, climbing through yellow and green, then
cyan/blue, magenta, and finally red at the highest altitudes (51,000ft+).
"""

from __future__ import annotations

import colorsys

# Altitude (ft) -> hue breakpoints. Linearly interpolated between entries;
# altitudes outside the range clamp to the nearest endpoint's hue.
_ALT_HUE_STOPS: list[tuple[float, float]] = [
    (0, 20),  # orange
    (2000, 32.5),  # yellow
    (4000, 43),  # yellow
    (6000, 54),  # yellow
    (8000, 72),  # yellow
    (9000, 85),  # green-yellow
    (11000, 140),  # light green
    (40000, 300),  # magenta
    (51000, 360),  # red
]

# Hue -> lightness (%) breakpoints, so the gradient stays visually
# balanced across hues rather than some colors reading darker/muddier
# than others at the same saturation.
_HUE_LIGHTNESS_STOPS: list[tuple[float, float]] = [
    (0, 53),
    (20, 50),
    (32, 54),
    (40, 52),
    (46, 51),
    (50, 46),
    (60, 43),
    (80, 41),
    (100, 41),
    (120, 41),
    (140, 41),
    (160, 40),
    (180, 40),
    (190, 44),
    (198, 50),
    (200, 58),
    (220, 58),
    (240, 58),
    (255, 55),
    (266, 55),
    (270, 58),
    (280, 58),
    (290, 47),
    (300, 43),
    (310, 48),
    (320, 48),
    (340, 52),
    (360, 53),
]

_AIR_SATURATION = 88

# Fixed colors for aircraft with no usable altitude, matching tar1090's
# "unknown" HSL entry (light gray).
UNKNOWN_COLOR = (191, 191, 191)  # HSL(0, 0%, 75%)


def _interp(stops: list[tuple[float, float]], x: float) -> float:
    """Linearly interpolate y for x across a sorted list of (x, y) stops,
    clamping to the first/last entry outside the covered range."""
    if x <= stops[0][0]:
        return stops[0][1]
    if x >= stops[-1][0]:
        return stops[-1][1]
    for (x0, y0), (x1, y1) in zip(stops, stops[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            frac = (x - x0) / (x1 - x0)
            return y0 + frac * (y1 - y0)
    return stops[-1][1]


def color_for_altitude(alt_ft) -> tuple[int, int, int]:
    """Return an RGB tuple for the given altitude in feet, matching
    ADS-B Exchange / tar1090's altitude color gradient. Returns a neutral
    gray for missing/invalid altitude."""
    try:
        alt = float(alt_ft)
    except (TypeError, ValueError):
        return UNKNOWN_COLOR

    hue = _interp(_ALT_HUE_STOPS, alt)
    lightness = _interp(_HUE_LIGHTNESS_STOPS, hue)

    r, g, b = colorsys.hls_to_rgb(hue / 360.0, lightness / 100.0, _AIR_SATURATION / 100.0)
    return (round(r * 255), round(g * 255), round(b * 255))
