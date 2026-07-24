"""Dead-reckon aircraft/vessel positions between ADS-B polls.

UI polls about every DATA_REFRESH_SECONDS (2s). Between updates, move each
track along heading×ground_speed so markers glide instead of jumping.
"""

from __future__ import annotations

import math
import time
from typing import Any


# Stop extrapolating if the track hasn't been observed recently.
_STALE_S = 8.0
# Cap how far ahead of the last report we push (covers a missed poll).
_MAX_EXTRAPOLATE_S = 5.0
# Ignore tiny speeds (parked vessels / floating noise).
_MIN_SPEED_KT = 1.0
# If a new fix disagrees with the coasted position by more than this, snap.
_SNAP_KM = 1.5


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _identity(flight: dict) -> str | None:
    if flight.get("kind") == "vessel":
        mmsi = str(flight.get("mmsi") or "").strip()
        if mmsi:
            return f"mmsi:{mmsi}"
    hex_id = (flight.get("icao_hex") or "").strip().upper()
    if hex_id:
        return f"hex:{hex_id}"
    callsign = (
        flight.get("callsign")
        or flight.get("flight_number")
        or flight.get("name")
        or ""
    ).strip().upper()
    if callsign:
        return f"cs:{callsign}"
    return None


def offset_lat_lon(
    lat: float,
    lon: float,
    heading_deg: float,
    speed_kt: float,
    dt_s: float,
) -> tuple[float, float]:
    """Move WGS84 point along true heading at ground speed for dt_s seconds."""
    if dt_s <= 0 or speed_kt < _MIN_SPEED_KT:
        return lat, lon
    dist_km = speed_kt * 1.852 * (dt_s / 3600.0)
    rad = math.radians(heading_deg % 360.0)
    d_north = dist_km * math.cos(rad)
    d_east = dist_km * math.sin(rad)
    dlat = d_north / 110.574
    cos_lat = max(0.01, math.cos(math.radians(lat)))
    dlon = d_east / (111.320 * cos_lat)
    return lat + dlat, lon + dlon


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat_rad = math.radians(lat1)
    dx = (lon2 - lon1) * 111.320 * math.cos(lat_rad)
    dy = (lat2 - lat1) * 110.574
    return math.hypot(dx, dy)


class PositionSmoother:
    """Track last reported kinematics and return coasted positions for draw."""

    def __init__(self) -> None:
        self._tracks: dict[str, dict[str, Any]] = {}
        from display.round_touch.trail_history import TrailHistory

        self.trails = TrailHistory()

    def reset(self) -> None:
        self._tracks.clear()
        self.trails.reset()

    def apply(self, flights: list[dict], now: float | None = None) -> list[dict]:
        """Return shallow copies with plane_latitude/longitude dead-reckoned to ``now``."""
        now = time.time() if now is None else float(now)
        self.trails.observe_many(flights, now=now)
        seen: set[str] = set()
        out: list[dict] = []

        for flight in flights:
            identity = _identity(flight)
            lat = _as_float(flight.get("plane_latitude"))
            lon = _as_float(flight.get("plane_longitude"))
            if identity is None or lat is None or lon is None:
                out.append(flight)
                continue

            seen.add(identity)
            heading = _as_float(flight.get("heading"))
            speed = _as_float(flight.get("ground_speed"))
            track = self._tracks.get(identity)

            if track is None:
                self._tracks[identity] = {
                    "lat": lat,
                    "lon": lon,
                    "report_lat": lat,
                    "report_lon": lon,
                    "heading": heading,
                    "speed": speed,
                    "t0": now,
                }
                track = self._tracks[identity]
            else:
                pos_changed = (
                    abs(track["report_lat"] - lat) > 1e-7
                    or abs(track["report_lon"] - lon) > 1e-7
                )
                if pos_changed:
                    coast_lat, coast_lon = lat, lon
                    if track.get("heading") is not None and track.get("speed") is not None:
                        coast_lat, coast_lon = offset_lat_lon(
                            track["lat"],
                            track["lon"],
                            track["heading"],
                            track["speed"],
                            min(max(0.0, now - track["t0"]), _MAX_EXTRAPOLATE_S),
                        )
                    err_km = _distance_km(coast_lat, coast_lon, lat, lon)
                    if err_km <= _SNAP_KM:
                        seed_lat, seed_lon = coast_lat, coast_lon
                    else:
                        seed_lat, seed_lon = lat, lon
                    track["lat"] = seed_lat
                    track["lon"] = seed_lon
                    track["report_lat"] = lat
                    track["report_lon"] = lon
                    track["t0"] = now
                    if heading is not None:
                        track["heading"] = heading
                    if speed is not None:
                        track["speed"] = speed
                else:
                    # Same reported fix — keep coasting; refresh kinematics if present.
                    if heading is not None:
                        track["heading"] = heading
                    if speed is not None:
                        track["speed"] = speed

            age = now - track["t0"]
            use_heading = track.get("heading")
            use_speed = track.get("speed")
            if (
                use_heading is None
                or use_speed is None
                or use_speed < _MIN_SPEED_KT
                or age < 0
                or age > _STALE_S
            ):
                out.append(flight)
                continue

            dt = min(age, _MAX_EXTRAPOLATE_S)
            slat, slon = offset_lat_lon(
                track["lat"], track["lon"], use_heading, use_speed, dt
            )
            smoothed = dict(flight)
            smoothed["plane_latitude"] = slat
            smoothed["plane_longitude"] = slon
            out.append(smoothed)

        for identity in list(self._tracks):
            if identity not in seen:
                del self._tracks[identity]

        return out
