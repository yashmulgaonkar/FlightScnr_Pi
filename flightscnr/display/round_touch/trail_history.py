"""Ring-buffer of recent lat/lon fixes for radar path drawing."""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Any


# Keep roughly a few minutes of samples at ~2s refresh.
_MAX_POINTS = 90
_MAX_AGE_S = 8 * 60.0
# Ignore jitter / duplicate reports closer than this.
_MIN_MOVE_KM = 0.05


def flight_identity(flight: dict) -> str | None:
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


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat_rad = math.radians(lat1)
    dx = (lon2 - lon1) * 111.320 * math.cos(lat_rad)
    dy = (lat2 - lat1) * 110.574
    return math.hypot(dx, dy)


def normalize_fr24_trail(raw) -> list[tuple[float, float]]:
    """FR24 / overhead trail → chronological (oldest→newest) lat/lon pairs."""
    if not raw:
        return []
    points: list[tuple[float, float]] = []
    for pt in raw:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            lat, lon = _as_float(pt[0]), _as_float(pt[1])
        elif isinstance(pt, dict):
            lat = _as_float(pt.get("lat"))
            lon = _as_float(pt.get("lng") if pt.get("lng") is not None else pt.get("lon"))
        else:
            continue
        if lat is None or lon is None:
            continue
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            continue
        points.append((lat, lon))
    # Overhead / web map treat FR24 trail as newest-first.
    if len(points) >= 2:
        points = list(reversed(points))
    return points


class TrailHistory:
    """Per-track ring buffer of observed positions."""

    def __init__(
        self,
        *,
        max_points: int = _MAX_POINTS,
        max_age_s: float = _MAX_AGE_S,
        min_move_km: float = _MIN_MOVE_KM,
    ) -> None:
        self._max_points = max(2, int(max_points))
        self._max_age_s = float(max_age_s)
        self._min_move_km = float(min_move_km)
        self._tracks: dict[str, deque[tuple[float, float, float]]] = {}

    def reset(self) -> None:
        self._tracks.clear()

    def observe(self, flight: dict, now: float | None = None) -> None:
        identity = flight_identity(flight)
        lat = _as_float(flight.get("plane_latitude"))
        lon = _as_float(flight.get("plane_longitude"))
        if identity is None or lat is None or lon is None:
            return
        now = time.time() if now is None else float(now)
        buf = self._tracks.get(identity)
        if buf is None:
            buf = deque(maxlen=self._max_points)
            self._tracks[identity] = buf
            buf.append((lat, lon, now))
            return
        if buf:
            plat, plon, _ = buf[-1]
            if _distance_km(plat, plon, lat, lon) < self._min_move_km:
                # Refresh timestamp on last point so age pruning stays honest.
                buf[-1] = (plat, plon, now)
                return
        buf.append((lat, lon, now))
        self._prune(identity, now)

    def observe_many(self, flights: list[dict], now: float | None = None) -> None:
        now = time.time() if now is None else float(now)
        seen: set[str] = set()
        for flight in flights:
            identity = flight_identity(flight)
            if identity:
                seen.add(identity)
            self.observe(flight, now=now)
        for identity in list(self._tracks):
            if identity not in seen:
                del self._tracks[identity]
            else:
                self._prune(identity, now)

    def _prune(self, identity: str, now: float) -> None:
        buf = self._tracks.get(identity)
        if not buf:
            return
        cutoff = now - self._max_age_s
        while buf and buf[0][2] < cutoff:
            buf.popleft()
        if not buf:
            del self._tracks[identity]

    def local_trail(self, identity: str | None, now: float | None = None) -> list[tuple[float, float]]:
        if not identity:
            return []
        now = time.time() if now is None else float(now)
        self._prune(identity, now)
        buf = self._tracks.get(identity)
        if not buf:
            return []
        return [(lat, lon) for lat, lon, _ in buf]

    def trail_for_flight(self, flight: dict | None, now: float | None = None) -> list[tuple[float, float]]:
        """Merged FR24 trail (if any) + local buffer, chronological, de-duped."""
        if not flight:
            return []
        identity = flight_identity(flight)
        local = self.local_trail(identity, now=now)
        remote = normalize_fr24_trail(flight.get("trail"))
        if not remote and not local:
            return []
        if not remote:
            return local
        if not local:
            return remote
        # Append local points that extend past the last remote fix.
        merged = list(remote)
        last = merged[-1]
        for lat, lon in local:
            if _distance_km(last[0], last[1], lat, lon) < self._min_move_km:
                continue
            merged.append((lat, lon))
            last = (lat, lon)
        if len(merged) > self._max_points:
            merged = merged[-self._max_points :]
        return merged
