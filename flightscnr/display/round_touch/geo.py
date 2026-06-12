"""Flat-earth geo helpers for radar projection."""

import math

try:
    from config import LOCATION_HOME
except ImportError:
    LOCATION_HOME = [0.0, 0.0]

from display.round_touch import scale, theme


def local_offset_km(lat: float, lon: float, center_lat=None, center_lon=None):
    if center_lat is None:
        center_lat = LOCATION_HOME[0]
    if center_lon is None:
        center_lon = LOCATION_HOME[1]

    lat_rad = math.radians(center_lat)
    dx_km = (lon - center_lon) * 111.320 * math.cos(lat_rad)
    dy_km = (lat - center_lat) * 110.574
    dist_km = math.hypot(dx_km, dy_km)
    return dx_km, dy_km, dist_km


def inner_ring_max_km():
    outer_km = scale.active_band()["label_km"]
    inset = theme.AIRCRAFT_ICON_RADIUS + theme.s(2)
    return outer_km * (
        (theme.GRID_OUTER_RADIUS - inset) / theme.GRID_OUTER_RADIUS
    )


def lat_lon_to_screen(lat: float, lon: float):
    outer_km = scale.active_band()["label_km"]
    px_per_km = theme.GRID_OUTER_RADIUS / outer_km
    dx_km, dy_km, _ = local_offset_km(lat, lon)
    x = theme.CENTER_X + int(round(dx_km * px_per_km))
    y = theme.CENTER_Y - int(round(dy_km * px_per_km))
    return x, y


def beyond_ring_position(lat: float, lon: float):
    dx_km, dy_km, dist_km = local_offset_km(lat, lon)
    if dist_km < 0.01 or dist_km <= inner_ring_max_km():
        return None
    rim_r = theme.VISIBLE_RADIUS - theme.BEYOND_RING_MARGIN
    angle = math.atan2(dx_km, dy_km)
    x = theme.CENTER_X + int(round(math.sin(angle) * rim_r))
    y = theme.CENTER_Y - int(round(math.cos(angle) * rim_r))
    return x, y
