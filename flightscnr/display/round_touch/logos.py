"""Airline logo loading for round touch display."""

from __future__ import annotations

import os

import pygame

try:
    from PIL import Image
except ImportError:
    Image = None

_DEFAULT = "default"
_cache: dict[tuple[str, int], pygame.Surface | None] = {}


def _package_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _logo_dir() -> str:
    base = _package_root()
    ref = os.path.join(base, "logos")
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


def _logo_path(icao: str) -> str | None:
    code = (icao or "").strip().upper()
    if not code or code == "N/A":
        code = _DEFAULT
    path = os.path.join(_logo_dir(), f"{code}.png")
    if os.path.isfile(path):
        return path
    fallback = os.path.join(_logo_dir(), f"{_DEFAULT}.png")
    return fallback if os.path.isfile(fallback) else None


def _trim_visible(image: "Image.Image", *, lum_min: int = 35, alpha_min: int = 32) -> "Image.Image":
    """Crop to pixels that read on the dark radar background.

    Banner logos often include near-black wordmarks on the right; centering the
    full PNG shifts the visible mark left. Trimming first keeps logos centered.
    """
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


def icao_for_flight(flight: dict) -> str:
    try:
        from utilities.airline_branding import resolve_logo_icao
    except ImportError:
        resolve_logo_icao = None

    if resolve_logo_icao is not None:
        icao = resolve_logo_icao(
            operator_icao=flight.get("owner_icao") or "",
            airline_icao=flight.get("airline_icao") or "",
            flight_number=flight.get("flight_number") or flight.get("number") or "",
            callsign=flight.get("callsign") or "",
        )
        if icao != "default":
            return icao

    callsign = (flight.get("callsign") or "").strip().upper()
    if len(callsign) >= 3 and callsign[:3].isalpha():
        return callsign[:3]
    return _DEFAULT


def load_logo_surface(icao: str, height: int) -> pygame.Surface | None:
    """Load an airline logo scaled to the given height; width follows aspect ratio."""
    if height <= 0 or Image is None:
        return None
    key = ((icao or "").upper(), height)
    if key in _cache:
        return _cache[key]

    surface = None
    path = _logo_path(icao)
    if path:
        try:
            image = _trim_visible(Image.open(path))
            src_w, src_h = image.size
            if src_h > 0:
                try:
                    resample = Image.Resampling.LANCZOS
                except AttributeError:
                    resample = Image.LANCZOS
                new_h = height
                new_w = max(1, int(round(src_w * height / src_h)))
                max_w = height * 6
                if new_w > max_w:
                    new_w = max_w
                    new_h = max(1, int(round(src_h * max_w / src_w)))
                image = image.resize((new_w, new_h), resample)
                surface = pygame.image.frombuffer(
                    image.tobytes(), image.size, "RGBA"
                ).convert_alpha()
        except OSError:
            surface = None

    _cache[key] = surface
    return surface
