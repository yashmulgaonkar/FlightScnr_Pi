"""Display rotation — logical draw buffer vs physical screen and touch."""

import pygame

from display.round_touch import theme


def normalize_degrees(degrees: int) -> int:
    degrees = int(degrees) % 360
    if degrees not in (0, 90, 180, 270):
        degrees = round(degrees / 90) * 90 % 360
    return degrees


def rotation_degrees() -> int:
    """Clockwise UI rotation (persisted settings, else DISPLAY_ROTATION env)."""
    try:
        from display.round_touch import settings

        return normalize_degrees(settings.display_rotation())
    except Exception:
        pass
    try:
        from config import DISPLAY_ROTATION
    except ImportError:
        import os

        try:
            DISPLAY_ROTATION = int(os.environ.get("DISPLAY_ROTATION", "0"))
        except (TypeError, ValueError):
            DISPLAY_ROTATION = 0
    return normalize_degrees(DISPLAY_ROTATION)


def to_logical(x: float, y: float) -> tuple[int, int]:
    """Map a physical screen/touch coordinate into the draw buffer."""
    side = theme.SIZE
    rotation = rotation_degrees()
    if rotation == 0:
        return int(x), int(y)
    if rotation == 90:
        return int(y), int(side - 1 - x)
    if rotation == 180:
        return int(side - 1 - x), int(side - 1 - y)
    return int(side - 1 - y), int(x)


def present(display: pygame.Surface, frame: pygame.Surface) -> None:
    """Blit the logical frame onto the physical display, applying rotation."""
    rotation = rotation_degrees()
    if rotation == 0:
        if display.get_size() == frame.get_size():
            display.blit(frame, (0, 0))
        else:
            display.fill((0, 0, 0))
            display.blit(frame, _center_offset(display, frame))
        return

    rotated = pygame.transform.rotate(frame, -rotation)
    display.fill((0, 0, 0))
    display.blit(rotated, _center_offset(display, rotated))


def _center_offset(dst: pygame.Surface, src: pygame.Surface) -> tuple[int, int]:
    return (
        (dst.get_width() - src.get_width()) // 2,
        (dst.get_height() - src.get_height()) // 2,
    )
