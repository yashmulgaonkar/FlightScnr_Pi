"""Inter UI font bundled with the app."""

from __future__ import annotations

import os

import pygame

_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BUNDLED_DIR = os.path.join(_PACKAGE_ROOT, "fonts", "inter")

_REGULAR = (
    "Inter-Regular.ttf",
    "Inter-Regular.otf",
)
_BOLD = (
    "Inter-Bold.ttf",
    "Inter-SemiBold.ttf",
    "Inter-Bold.otf",
)


def resolve_font_path(bold: bool = False) -> str | None:
    """Return Inter font path, or None to use DejaVu fallback."""
    for name in _BOLD if bold else _REGULAR:
        path = os.path.join(_BUNDLED_DIR, name)
        if os.path.isfile(path):
            return path
    for name in ("inter", "inter variable"):
        path = pygame.font.match_font(name, bold=bold)
        if path:
            return path
    return None
