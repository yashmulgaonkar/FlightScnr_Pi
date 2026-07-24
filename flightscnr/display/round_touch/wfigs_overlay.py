"""NIFC WFIGS current wildfire incident locations (AirNow Fire and Smoke Map source).

Public ArcGIS FeatureServer (no API key):
https://services3.arcgis.com/T4QMspbfLg3qTGWY/ArcGIS/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

from display.round_touch import firms_overlay

logger = logging.getLogger("flightscnr.display")

FEATURE_URL = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/ArcGIS/rest/services/"
    "WFIGS_Incident_Locations_Current/FeatureServer/0/query"
)
OUT_FIELDS = (
    "IncidentName,POOState,POOCounty,IncidentSize,PercentContained,"
    "FireDiscoveryDateTime,POOCity,UniqueFireIdentifier,"
    "IncidentShortDescription,POOProtectingAgency,IrwinID,"
    "DiscoveryAcres,FinalAcres"
)
USER_AGENT = "FlightScnrPi/1.0 (NIFC WFIGS overlay; +https://github.com/yashmulgaonkar/FlightScnr_Pi)"
FETCH_TIMEOUT_S = 25
POLL_TTL_S = 15 * 60
PAGE_TIMEOUT_S = 20
MAX_FEATURES = 200
BBOX_MARGIN = firms_overlay.BBOX_MARGIN
_TOPO_EXPORT = (
    "https://services.arcgisonline.com/ArcGIS/rest/services/"
    "World_Topo_Map/MapServer/export"
)

_lock = threading.Lock()
_fires: list[dict[str, Any]] = []
_fires_key: tuple | None = None
_fires_ts = 0.0
_fetch_thread: threading.Thread | None = None
_force_refresh = False
_map_inflight: set[str] = set()
_map_miss: set[str] = set()


def _data_dir() -> str:
    return os.environ.get("FLIGHTSCNR_DATA_DIR", "/var/lib/flightscnr")


def _maps_dir() -> str:
    path = os.path.join(_data_dir(), "wfigs_maps")
    os.makedirs(path, exist_ok=True)
    return path


def _enabled() -> bool:
    try:
        from display.round_touch import settings, wildfire_overlay

        # USA/Canada outside California only (CA uses CAL FIRE).
        if not wildfire_overlay.using_wfigs():
            return False
        return bool(settings.show_wildfires())
    except Exception:
        return False


def _cache_key() -> tuple | None:
    try:
        from config import LOCATION_HOME, location_configured
        from display.round_touch import settings
    except ImportError:
        return None
    if not location_configured():
        return None
    return (
        round(float(LOCATION_HOME[0]), 5),
        round(float(LOCATION_HOME[1]), 5),
        int(settings.scale_index()),
        "wfigs",
    )


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, */*",
        }
    )
    return s


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_discovery(value: Any) -> str | None:
    """FireDiscoveryDateTime is usually epoch ms; return YYYY-MM-DD when possible."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            ms = float(value)
            if ms > 1e12:
                ms /= 1000.0
            return datetime.fromtimestamp(ms, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return _format_discovery(int(text))
    if "T" in text:
        return text.split("T", 1)[0]
    return text[:10] if len(text) >= 10 else text


def _coords_from_feature(feat: dict[str, Any]) -> tuple[float, float] | None:
    geom = feat.get("geometry") if isinstance(feat, dict) else None
    if isinstance(geom, dict):
        coords = geom.get("coordinates")
        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
            lon = _as_float(coords[0])
            lat = _as_float(coords[1])
            if lat is not None and lon is not None:
                return lat, lon
        # ArcGIS JSON (non-GeoJSON) point
        lat = _as_float(geom.get("y"))
        lon = _as_float(geom.get("x"))
        if lat is not None and lon is not None:
            return lat, lon
    props = feat.get("properties") or feat.get("attributes") or {}
    if not isinstance(props, dict):
        return None
    lat = _as_float(props.get("POOLatitude") or props.get("InitialLatitude"))
    lon = _as_float(props.get("POOLongitude") or props.get("InitialLongitude"))
    if lat is not None and lon is not None:
        return lat, lon
    return None


def parse_features(
    features: list[dict[str, Any]],
    *,
    center_lat: float,
    center_lon: float,
    radius_km: float,
) -> list[dict[str, Any]]:
    """Normalize WFIGS GeoJSON/JSON features within radius_km of center."""
    max_r = max(1.0, float(radius_km))
    out: list[dict[str, Any]] = []
    for feat in features or []:
        if not isinstance(feat, dict):
            continue
        coords = _coords_from_feature(feat)
        if coords is None:
            continue
        lat, lon = coords
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            continue
        d_km = math.hypot((lat - center_lat) * 110.574, (lon - center_lon) * 85.0)
        if d_km > max_r:
            continue
        props = feat.get("properties") or feat.get("attributes") or {}
        if not isinstance(props, dict):
            props = {}
        uid = (
            str(props.get("UniqueFireIdentifier") or props.get("IrwinID") or "").strip()
            or f"{lat:.5f},{lon:.5f}"
        )
        acres = _as_float(props.get("IncidentSize"))
        if acres is None:
            acres = _as_float(props.get("FinalAcres"))
        if acres is None:
            acres = _as_float(props.get("DiscoveryAcres"))
        containment = _as_float(props.get("PercentContained"))
        county = (props.get("POOCounty") or "").strip() or None
        state = (props.get("POOState") or "").strip() or None
        if state and state.startswith("US-"):
            state = state[3:]
        city = (props.get("POOCity") or "").strip() or None
        short = (props.get("IncidentShortDescription") or "").strip() or None
        region = ", ".join(x for x in (county, state) if x)
        location = short or city or (region or None)
        name = (props.get("IncidentName") or "Wildfire").strip() or "Wildfire"
        admin = (props.get("POOProtectingAgency") or "").strip() or None
        out.append(
            {
                "source": "wfigs",
                "id": uid,
                "lat": lat,
                "lon": lon,
                "name": name,
                "county": county,
                "acres": acres,
                "containment": containment,
                "location": location,
                "started": _format_discovery(props.get("FireDiscoveryDateTime")),
                "admin_unit": admin,
                "state": state,
            }
        )
    out.sort(key=lambda f: (-(f.get("acres") or 0.0), f.get("name") or ""))
    return out


def fetch_fires_for_center(
    lat: float, lon: float, radius_km: float
) -> list[dict[str, Any]]:
    """Blocking WFIGS fetch — call from a background thread."""
    west, south, east, north = firms_overlay.bbox_from_center(
        lat, lon, radius_km, margin=BBOX_MARGIN
    )
    try:
        resp = _session().get(
            FEATURE_URL,
            params={
                "where": "1=1",
                "geometry": f"{west},{south},{east},{north}",
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": OUT_FIELDS,
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "geojson",
                "resultRecordCount": str(MAX_FEATURES),
            },
            timeout=FETCH_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("WFIGS fetch failed: %s", exc)
        return []
    if isinstance(data, dict) and data.get("error"):
        logger.warning("WFIGS query error: %s", data.get("error"))
        return []
    features = []
    if isinstance(data, dict):
        features = data.get("features") or []
    if not isinstance(features, list):
        logger.warning("WFIGS unexpected payload")
        return []
    return parse_features(
        features, center_lat=lat, center_lon=lon, radius_km=radius_km
    )


def _bg_fetch() -> None:
    global _fires, _fires_key, _fires_ts, _fetch_thread, _force_refresh
    try:
        from config import LOCATION_HOME
        from display.round_touch import geo

        key = _cache_key()
        points = fetch_fires_for_center(
            float(LOCATION_HOME[0]),
            float(LOCATION_HOME[1]),
            float(geo.visible_max_km()),
        )
        with _lock:
            _fires = points
            _fires_key = key
            _fires_ts = time.time()
            _force_refresh = False
        logger.info("WFIGS: %d incident(s) in radar area", len(points))
    except Exception:
        logger.exception("WFIGS background fetch failed")
    finally:
        with _lock:
            _fetch_thread = None


def invalidate() -> None:
    global _fires, _fires_key, _fires_ts, _force_refresh
    with _lock:
        _fires = []
        _fires_key = None
        _fires_ts = 0.0
        _force_refresh = True
        _map_miss.clear()
        _map_inflight.clear()


def request_refresh(*, force: bool = False) -> None:
    """Kick a background WFIGS refresh when enabled and stale/forced."""
    global _fetch_thread, _force_refresh
    if not _enabled():
        return
    key = _cache_key()
    if key is None:
        return
    with _lock:
        if force:
            _force_refresh = True
        need = (
            _force_refresh
            or _fires_key != key
            or (time.time() - _fires_ts) >= POLL_TTL_S
        )
        if not need or _fetch_thread is not None:
            return
        _fetch_thread = threading.Thread(target=_bg_fetch, daemon=True, name="wfigs")
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
    return "NIFC WFIGS"


def fires_by_distance() -> list[dict[str, Any]]:
    from display.round_touch import geo

    def key(f: dict[str, Any]) -> float:
        try:
            return geo.local_offset_km(f["lat"], f["lon"])[2]
        except Exception:
            return 1e9

    return sorted(get_fires(), key=key)


def _bbox_around(lat: float, lon: float, *, span_deg: float = 0.35) -> tuple[float, float, float, float]:
    half = max(0.08, float(span_deg) / 2.0)
    return lon - half, lat - half, lon + half, lat + half


def _write_map_file(fire_id: str, data: bytes, *, suffix: str = ".png") -> str | None:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", fire_id)[:80] or "fire"
    path = os.path.join(_maps_dir(), f"{safe}{suffix}")
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        return path
    except OSError as exc:
        logger.warning("Could not cache WFIGS map %s: %s", path, exc)
        return None


def _topo_export_map(lat: float, lon: float, fire_id: str) -> str | None:
    """Static topo snapshot with fire_icon.png marker."""
    west, south, east, north = _bbox_around(lat, lon)
    try:
        resp = _session().get(
            _TOPO_EXPORT,
            params={
                "bbox": f"{west},{south},{east},{north}",
                "bboxSR": "4326",
                "imageSR": "4326",
                "size": "480,360",
                "format": "png32",
                "transparent": "false",
                "f": "image",
            },
            timeout=PAGE_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.content
    except Exception as exc:
        logger.debug("WFIGS topo export failed: %s", exc)
        return None
    if not data or len(data) < 200:
        return None
    try:
        from io import BytesIO

        from PIL import Image

        image = Image.open(BytesIO(data)).convert("RGBA")
        w, h = image.size
        x = int((lon - west) / max(1e-9, (east - west)) * (w - 1))
        y = int((north - lat) / max(1e-9, (north - south)) * (h - 1))
        icon_path = os.path.normpath(
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..",
                "..",
                "assets",
                "fire_icon.png",
            )
        )
        icon = Image.open(icon_path).convert("RGBA")
        icon_h = max(28, min(w, h) // 10)
        icon_w = max(20, int(round(icon.width * (icon_h / float(icon.height)))))
        try:
            resample = Image.Resampling.LANCZOS
        except AttributeError:
            resample = Image.LANCZOS
        icon = icon.resize((icon_w, icon_h), resample)
        paste_x = int(x - icon_w // 2)
        paste_y = int(y - int(icon_h * 0.72))
        image.alpha_composite(icon, (paste_x, paste_y))
        buf = BytesIO()
        image.save(buf, format="PNG")
        data = buf.getvalue()
    except Exception:
        logger.debug("Could not overlay fire icon on WFIGS topo map", exc_info=True)
    return _write_map_file(fire_id, data, suffix=".png")


def fetch_map_for_fire(fire: dict[str, Any]) -> str | None:
    fire_id = str(fire.get("id") or "").strip()
    if not fire_id:
        return None
    lat = _as_float(fire.get("lat"))
    lon = _as_float(fire.get("lon"))
    if lat is None or lon is None:
        return None
    return _topo_export_map(lat, lon, fire_id)


def request_map(fire: dict[str, Any], on_done=None) -> None:
    """Background-fetch a topo map image; optional callback(path|None)."""
    fire_id = str(fire.get("id") or "").strip()
    if not fire_id:
        if on_done:
            on_done(None)
        return
    with _lock:
        if fire_id in _map_miss or fire_id in _map_inflight:
            if on_done and fire_id in _map_miss:
                on_done(None)
            return
        _map_inflight.add(fire_id)

    snapshot = dict(fire)

    def _work() -> None:
        path = None
        try:
            path = fetch_map_for_fire(snapshot)
        except Exception:
            logger.exception("WFIGS map fetch failed for %s", fire_id)
        finally:
            with _lock:
                _map_inflight.discard(fire_id)
                if not path:
                    _map_miss.add(fire_id)
        if on_done:
            on_done(path)

    threading.Thread(target=_work, daemon=True, name="wfigs-map").start()
