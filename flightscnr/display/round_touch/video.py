"""SDL video driver selection with Pi-friendly fallbacks."""

import logging
import os

import pygame

logger = logging.getLogger("flightscnr.display")

# Common Pi drivers, in rough order of preference when not under a desktop session.
_FALLBACK_DRIVERS = ("fbcon", "directfb", "x11", "wayland", "dummy")


def _driver_candidates():
    try:
        from config import SDL_VIDEODRIVER
    except ImportError:
        SDL_VIDEODRIVER = os.environ.get("SDL_VIDEODRIVER", "")

    preferred = (SDL_VIDEODRIVER or os.environ.get("SDL_VIDEODRIVER", "")).strip()
    candidates = []
    if preferred:
        candidates.append(preferred)
    candidates.append(None)  # SDL auto-detect
    for driver in _FALLBACK_DRIVERS:
        if driver not in candidates:
            candidates.append(driver)
    return candidates


def _set_driver(driver):
    if driver:
        os.environ["SDL_VIDEODRIVER"] = driver
    else:
        os.environ.pop("SDL_VIDEODRIVER", None)


def init_display(width: int, height: int, fullscreen: bool) -> pygame.Surface:
    """Initialize pygame and open the display, trying multiple SDL drivers."""
    flags = pygame.FULLSCREEN if fullscreen else 0
    last_error = None

    for driver in _driver_candidates():
        if pygame.get_init():
            pygame.display.quit()
            pygame.quit()

        _set_driver(driver)
        label = driver or "auto"

        try:
            pygame.init()
            pygame.display.set_caption("FlightScnr Pi")
            surface = pygame.display.set_mode((width, height), flags)
            logger.info("Display opened (%dx%d, driver=%s)", width, height, label)
            return surface
        except pygame.error as exc:
            last_error = exc
            logger.warning("SDL driver '%s' unavailable: %s", label, exc)

    raise RuntimeError(
        "Could not open display with any SDL video driver. "
        "Try setting SDL_VIDEODRIVER=fbcon in /etc/flightscnr.env, "
        "or run under the Pi desktop (X11). "
        f"Last error: {last_error}"
    ) from last_error
