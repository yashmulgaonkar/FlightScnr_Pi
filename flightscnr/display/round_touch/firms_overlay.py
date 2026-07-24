"""NASA FIRMS wildfire detections for the circular radar.

Area CSV API (free MAP_KEY):
https://firms.modaps.eosdis.nasa.gov/api/area/
"""

from __future__ import annotations

import csv
import io
import logging
import math
import os
import threading
import time
from typing import Any

import pygame
import requests

from display.round_touch import geo, theme

logger = logging.getLogger("flightscnr.display")

API_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
# 375 m NRT — good regional coverage; one transaction per poll.
SOURCE = "VIIRS_SNPP_NRT"
DAY_RANGE = 1
USER_AGENT = "FlightScnrPi/1.0 (NASA FIRMS overlay)"
FETCH_TIMEOUT_S = 25
POLL_TTL_S = 15 * 60
BBOX_MARGIN = 1.15
# Drop detections older than this (acq_date + acq_time). day_range=1 still
# includes a full calendar day; this keeps the radar on currently active fires.
MAX_AGE_HOURS = 24.0
# VIIRS: l/n/h. Low daytime pixels are often sun-glint / weak anomalies.
_ACTIVE_CONFIDENCE = frozenset({"n", "h", "nominal", "high"})

_ICON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "..",
    "assets",
    "fire_icon.png",
)
# Marker height in theme units — smaller than aircraft icons (22–34).
_ICON_HEIGHT = 14

_lock = threading.Lock()
_fires: list[dict[str, Any]] = []
_fires_key: tuple | None = None
_fires_ts = 0.0
_icon_cache: dict[int, pygame.Surface] = {}
_icon_warned = False
_fetch_thread: threading.Thread | None = None
_force_refresh = False


def _map_key() -> str:
    try:
        from config import FIRMS_MAP_KEY

        return (FIRMS_MAP_KEY or "").strip()
    except ImportError:
        import os

        return os.environ.get("FIRMS_MAP_KEY", "").strip()


def _enabled() -> bool:
    if not _map_key():
        return False
    try:
        from display.round_touch import settings, wildfire_overlay

        # USA/Canada use CAL FIRE (CA) or NIFC WFIGS; FIRMS is rest-of-world only.
        if not wildfire_overlay.using_firms():
            return False
        return bool(settings.show_wildfires())
    except Exception:
        return False


def bbox_from_center(
    lat: float, lon: float, radius_km: float, *, margin: float = BBOX_MARGIN
) -> tuple[float, float, float, float]:
    """Return west,south,east,north for FIRMS area API."""
    r = max(1.0, float(radius_km) * float(margin))
    dlat = r / 110.574
    cos_lat = max(0.01, math.cos(math.radians(lat)))
    dlon = r / (111.320 * cos_lat)
    west = max(-180.0, lon - dlon)
    east = min(180.0, lon + dlon)
    south = max(-90.0, lat - dlat)
    north = min(90.0, lat + dlat)
    return west, south, east, north


def _cache_key() -> tuple | None:
    try:
        from config import LOCATION_HOME, location_configured
        from display.round_touch import scale, settings
    except ImportError:
        return None
    if not location_configured():
        return None
    return (
        round(float(LOCATION_HOME[0]), 5),
        round(float(LOCATION_HOME[1]), 5),
        int(settings.scale_index()),
        SOURCE,
        DAY_RANGE,
    )


def _parse_acq_unix(row: dict) -> float | None:
    """Parse FIRMS acq_date (YYYY-MM-DD) + acq_time (HHMM) to unix seconds (UTC)."""
    from datetime import datetime, timezone

    date_s = (row.get("acq_date") or row.get("Acq_Date") or "").strip()
    time_s = str(row.get("acq_time") or row.get("Acq_Time") or "").strip()
    if not date_s:
        return None
    try:
        if time_s.isdigit():
            time_s = time_s.zfill(4)
            hour = int(time_s[:2])
            minute = int(time_s[2:4])
        else:
            hour, minute = 0, 0
        dt = datetime.strptime(date_s, "%Y-%m-%d").replace(
            hour=hour, minute=minute, tzinfo=timezone.utc
        )
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def _confidence_is_active(confidence: str) -> bool:
    conf = (confidence or "").strip().lower()
    if not conf:
        # Missing confidence: keep (treat as unknown but still a detection).
        return True
    if conf in _ACTIVE_CONFIDENCE:
        return True
    # MODIS uses 0–100 numeric confidence.
    try:
        return float(conf) >= 50.0
    except (TypeError, ValueError):
        return False


def _hotspot_type_is_vegetation(row: dict) -> bool:
    """Standard products may include type: 0=vegetation fire. Drop volcano/static/offshore."""
    raw = row.get("type") or row.get("Type")
    if raw is None or raw == "":
        return True
    try:
        return int(float(raw)) == 0
    except (TypeError, ValueError):
        return True


def is_active_fire(
    row: dict,
    *,
    now: float | None = None,
    max_age_hours: float = MAX_AGE_HOURS,
) -> bool:
    """True for recent, non-low-confidence vegetation fire detections."""
    confidence = (row.get("confidence") or row.get("Confidence") or "").strip()
    if not _confidence_is_active(confidence):
        return False
    if not _hotspot_type_is_vegetation(row):
        return False
    acq = _parse_acq_unix(row)
    if acq is not None:
        age_h = ((time.time() if now is None else now) - acq) / 3600.0
        if age_h < -1.0:
            # Clock skew / future stamp — keep.
            return True
        if age_h > max_age_hours:
            return False
    return True


def parse_firms_csv(text: str, *, now: float | None = None) -> list[dict[str, Any]]:
    """Parse FIRMS area CSV into active fire point dicts."""
    text = (text or "").strip()
    if not text or text.lower().startswith("invalid") or text.lower().startswith("<!"):
        return []
    reader = csv.DictReader(io.StringIO(text))
    out: list[dict[str, Any]] = []
    for row in reader:
        try:
            lat = float(row.get("latitude") or row.get("Latitude") or "")
            lon = float(row.get("longitude") or row.get("Longitude") or "")
        except (TypeError, ValueError):
            continue
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            continue
        if not is_active_fire(row, now=now):
            continue
        frp = None
        for key in ("frp", "FRP"):
            raw = row.get(key)
            if raw is None or raw == "":
                continue
            try:
                frp = float(raw)
            except (TypeError, ValueError):
                frp = None
            break
        confidence = (row.get("confidence") or row.get("Confidence") or "").strip()
        out.append(
            {
                "source": "firms",
                "id": f"firms:{lat:.4f},{lon:.4f}",
                "lat": lat,
                "lon": lon,
                "name": "Hotspot",
                "frp": frp,
                "confidence": confidence,
            }
        )
    return out


def _fetch_url(map_key: str, west: float, south: float, east: float, north: float) -> str:
    area = f"{west:.5f},{south:.5f},{east:.5f},{north:.5f}"
    return f"{API_BASE}/{map_key}/{SOURCE}/{area}/{DAY_RANGE}"


def fetch_fires_for_center(
    lat: float, lon: float, radius_km: float, *, map_key: str | None = None
) -> list[dict[str, Any]]:
    """Blocking FIRMS fetch — call from a background thread."""
    key = (map_key if map_key is not None else _map_key()).strip()
    if not key:
        return []
    west, south, east, north = bbox_from_center(lat, lon, radius_km)
    url = _fetch_url(key, west, south, east, north)
    try:
        resp = requests.get(
            url,
            timeout=FETCH_TIMEOUT_S,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        return parse_firms_csv(resp.text)
    except (OSError, requests.RequestException) as exc:
        logger.warning("FIRMS fetch failed: %s", exc)
        return []


def _do_fetch(key: tuple) -> None:
    global _fires, _fires_key, _fires_ts, _fetch_thread, _force_refresh
    try:
        from config import LOCATION_HOME

        radius_km = geo.visible_max_km()
        points = fetch_fires_for_center(
            float(LOCATION_HOME[0]),
            float(LOCATION_HOME[1]),
            radius_km,
        )
        with _lock:
            _fires = points
            _fires_key = key
            _fires_ts = time.time()
            _force_refresh = False
        logger.info("FIRMS: %d fire detection(s) in radar area", len(points))
    except Exception:
        logger.exception("FIRMS background fetch failed")
    finally:
        with _lock:
            _fetch_thread = None


def invalidate() -> None:
    """Drop cache so the next request_refresh fetches again."""
    global _fires, _fires_key, _fires_ts, _force_refresh
    with _lock:
        _fires = []
        _fires_key = None
        _fires_ts = 0.0
        _force_refresh = True


def request_refresh(*, force: bool = False) -> None:
    """Kick a background FIRMS refresh when enabled and stale/forced."""
    global _fetch_thread, _force_refresh
    if not _enabled():
        return
    key = _cache_key()
    if key is None:
        return
    with _lock:
        if force:
            _force_refresh = True
        stale = (
            _force_refresh
            or _fires_key != key
            or (time.time() - _fires_ts) >= POLL_TTL_S
        )
        if not stale:
            return
        if _fetch_thread is not None and _fetch_thread.is_alive():
            return
        _fetch_thread = threading.Thread(
            target=_do_fetch,
            args=(key,),
            name="firms-fetch",
            daemon=True,
        )
        _fetch_thread.start()


def get_fires() -> list[dict[str, Any]]:
    if not _enabled():
        return []
    with _lock:
        return list(_fires)


def attribution_text() -> str | None:
    if not _enabled():
        return None
    with _lock:
        if not _fires and _fires_ts <= 0:
            return None
    return "NASA FIRMS"


def _icon_height(fire: dict[str, Any]) -> int:
    """Small flame height; bump slightly for hotter FRP."""
    base = max(10, theme.s(_ICON_HEIGHT))
    frp = fire.get("frp")
    if frp is None:
        return base
    try:
        frp_f = float(frp)
    except (TypeError, ValueError):
        return base
    if frp_f >= 50:
        return base + theme.s(3)
    if frp_f >= 10:
        return base + theme.s(2)
    return base


def _fire_icon(height: int) -> pygame.Surface | None:
    """Load and scale fire_icon.png to the given height (cached)."""
    global _icon_warned
    height = max(8, int(height))
    cached = _icon_cache.get(height)
    if cached is not None:
        return cached
    path = os.path.normpath(_ICON_PATH)
    try:
        image = pygame.image.load(path).convert_alpha()
    except (pygame.error, FileNotFoundError, OSError) as exc:
        if not _icon_warned:
            _icon_warned = True
            logger.warning("Could not load fire icon %s: %s", path, exc)
        return None
    src_w, src_h = image.get_size()
    if src_h <= 0:
        return None
    width = max(6, int(round(src_w * (height / float(src_h)))))
    scaled = pygame.transform.smoothscale(image, (width, height))
    _icon_cache[height] = scaled
    return scaled


def draw_fires(
    surface: pygame.Surface, pan_offset: tuple[int, int] | None = None
) -> None:
    """Draw small fire icons inside the visible radar circle."""
    fires = get_fires()
    if not fires:
        return
    from display.round_touch import map_bg

    ox = int(pan_offset[0]) if pan_offset else 0
    oy = int(pan_offset[1]) if pan_offset else 0
    max_r = theme.VISIBLE_RADIUS - theme.s(2)
    cx, cy = theme.CENTER_X, theme.CENTER_Y
    for fire in fires:
        try:
            # Match basemap Mercator placement (not flat-earth aircraft math) so
            # markers sit on the correct coastline/roads away from center.
            pos = map_bg.lat_lon_to_basemap_screen(fire["lat"], fire["lon"])
            if pos is None:
                pos = geo.lat_lon_to_screen(fire["lat"], fire["lon"])
            x, y = pos
        except Exception:
            continue
        x += ox
        y += oy
        if math.hypot(x - cx, y - cy) > max_r:
            continue
        icon = _fire_icon(_icon_height(fire))
        if icon is not None:
            rect = icon.get_rect(center=(int(x), int(y)))
            surface.blit(icon, rect)
        else:
            r = max(2, theme.s(3))
            pygame.draw.circle(surface, (255, 0, 0), (int(x), int(y)), r)
