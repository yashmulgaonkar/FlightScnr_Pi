"""Radar accent themes — from FlightScnr include/ui/radar_accent.cpp."""

from __future__ import annotations

# Order matches FlightScnr RadarAccentColor enum (Red → White).
THEME_NAMES = ("Red", "Yellow", "Orange", "Green", "White")

# grid, sweep, sweep_trail, label — background & aircraft stay fixed (radar_theme.h).
THEMES: tuple[dict[str, tuple[int, int, int]], ...] = (
    {
        "grid": (200, 24, 24),
        "sweep": (255, 64, 64),
        "sweep_trail": (72, 8, 8),
        "label": (255, 80, 80),
    },
    {
        "grid": (180, 180, 0),
        "sweep": (255, 255, 64),
        "sweep_trail": (72, 72, 0),
        "label": (255, 255, 128),
    },
    {
        "grid": (200, 100, 0),
        "sweep": (255, 180, 48),
        "sweep_trail": (80, 40, 0),
        "label": (255, 200, 64),
    },
    {
        "grid": (48, 220, 80),
        "crosshair": (48, 220, 80),
        "sweep": (80, 255, 112),
        "sweep_trail": (20, 90, 40),
        "label": (160, 255, 180),
    },
    {
        "grid": (160, 160, 160),
        "sweep": (255, 255, 255),
        "sweep_trail": (80, 80, 80),
        "label": (255, 255, 255),
    },
)

DEFAULT_THEME_INDEX = 3  # Green

THEME_COUNT = len(THEME_NAMES)
