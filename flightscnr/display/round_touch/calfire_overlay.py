"""CAL FIRE active wildfire incidents for California.

JSON API (no key):
https://incidents.fire.ca.gov/umbraco/api/IncidentApi/List?inactive=false
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from html import unescape
from typing import Any
from urllib.parse import urljoin

import requests

logger = logging.getLogger("flightscnr.display")

API_URL = "https://incidents.fire.ca.gov/umbraco/api/IncidentApi/List"
USER_AGENT = "FlightScnrPi/1.0 (CAL FIRE overlay; +https://github.com/yashmulgaonkar/FlightScnr_Pi)"
FETCH_TIMEOUT_S = 25
POLL_TTL_S = 15 * 60
PAGE_TIMEOUT_S = 20
# California mainland + roughly coastal waters (WFIGS covers other USA/Canada).
_CA_LAT = (32.52, 42.02)
_CA_LON = (-124.48, -114.12)
# ArcGIS World Topo — same family as the Esri map embedded on incident pages.
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


def in_california(lat: float, lon: float) -> bool:
    """True when coordinates fall inside a California bounding box."""
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        return False
    return _CA_LAT[0] <= lat_f <= _CA_LAT[1] and _CA_LON[0] <= lon_f <= _CA_LON[1]


def home_in_california() -> bool:
    try:
        from config import LOCATION_HOME, location_configured
    except ImportError:
        return False
    if not location_configured():
        return False
    return in_california(LOCATION_HOME[0], LOCATION_HOME[1])


def _data_dir() -> str:
    return os.environ.get("FLIGHTSCNR_DATA_DIR", "/var/lib/flightscnr")


def _maps_dir() -> str:
    path = os.path.join(_data_dir(), "calfire_maps")
    os.makedirs(path, exist_ok=True)
    return path


def _enabled() -> bool:
    if not home_in_california():
        return False
    try:
        from display.round_touch import settings

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
        "calfire",
    )


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/html, */*",
            "Referer": "https://www.fire.ca.gov/incidents",
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


def _incident_page_url(url: str | None) -> str | None:
    if not url:
        return None
    text = str(url).strip()
    if not text:
        return None
    if text.startswith("/"):
        text = "https://incidents.fire.ca.gov" + text
    return text.replace("https://www.fire.ca.gov", "https://incidents.fire.ca.gov")


def parse_incidents(
    rows: list[dict[str, Any]],
    *,
    center_lat: float,
    center_lon: float,
    radius_km: float,
) -> list[dict[str, Any]]:
    """Normalize CAL FIRE List JSON and keep incidents within radius_km of center."""
    max_r = max(1.0, float(radius_km))
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if row.get("IsActive") is False:
            continue
        if row.get("Final") is True:
            continue
        lat = _as_float(row.get("Latitude"))
        lon = _as_float(row.get("Longitude"))
        if lat is None or lon is None:
            continue
        if not in_california(lat, lon):
            continue
        # Rough km distance (same scale as geo helpers near CA latitudes).
        d_km = math.hypot((lat - center_lat) * 110.574, (lon - center_lon) * 85.0)
        if d_km > max_r:
            continue
        uid = str(row.get("UniqueId") or "").strip() or f"{lat:.5f},{lon:.5f}"
        acres = _as_float(row.get("AcresBurned"))
        containment = _as_float(row.get("PercentContained"))
        out.append(
            {
                "source": "calfire",
                "id": uid,
                "lat": lat,
                "lon": lon,
                "name": (row.get("Name") or "Wildfire").strip() or "Wildfire",
                "county": (row.get("County") or "").strip() or None,
                "acres": acres,
                "containment": containment,
                "location": (row.get("Location") or "").strip() or None,
                "started": (row.get("StartedDateOnly") or row.get("Started") or "").strip()
                or None,
                "updated": (row.get("Updated") or "").strip() or None,
                "url": (row.get("Url") or "").strip() or None,
                "admin_unit": (row.get("AdminUnit") or "").strip() or None,
                "type": (row.get("Type") or "").strip() or None,
            }
        )
    out.sort(key=lambda f: (-(f.get("acres") or 0.0), f.get("name") or ""))
    return out


def fetch_fires_for_center(
    lat: float, lon: float, radius_km: float
) -> list[dict[str, Any]]:
    """Blocking CAL FIRE fetch — call from a background thread."""
    try:
        resp = _session().get(
            API_URL,
            params={"inactive": "false"},
            timeout=FETCH_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("CAL FIRE fetch failed: %s", exc)
        return []
    if not isinstance(data, list):
        logger.warning("CAL FIRE unexpected payload type: %s", type(data).__name__)
        return []
    return parse_incidents(data, center_lat=lat, center_lon=lon, radius_km=radius_km)


def _bg_fetch() -> None:
    global _fires, _fires_key, _fires_ts, _fetch_thread, _force_refresh
    try:
        from config import LOCATION_HOME
        from display.round_touch import geo

        key = _cache_key()
        points = fetch_fires_for_center(
            float(LOCATION_HOME[0]),
            float(LOCATION_HOME[1]),
            float(geo.visible_max_km()) * 1.15,
        )
        with _lock:
            _fires = points
            _fires_key = key
            _fires_ts = time.time()
            _force_refresh = False
        logger.info("CAL FIRE: %d active incident(s) in radar area", len(points))
    except Exception:
        logger.exception("CAL FIRE background fetch failed")
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
    """Kick a background CAL FIRE refresh when enabled and stale/forced."""
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
        _fetch_thread = threading.Thread(target=_bg_fetch, daemon=True, name="calfire")
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
    return "CAL FIRE"


def fires_by_distance() -> list[dict[str, Any]]:
    from display.round_touch import geo

    def key(f: dict[str, Any]) -> float:
        try:
            return geo.local_offset_km(f["lat"], f["lon"])[2]
        except Exception:
            return 1e9

    return sorted(get_fires(), key=key)


def _looks_like_map_image(url: str) -> bool:
    """True only for stills that look like incident maps — not agency logos."""
    low = url.lower()
    if not re.search(r"\.(png|jpe?g|webp)(\?|$)", low):
        return False
    # Agency badges / chrome (0001_calfire.png was matching on "fire").
    if re.search(
        r"logo|icon|header|apple-touch|favicon|acres|personnel|crew|"
        r"agency|calfire|shield|blm|usfs|transparent|custom-icons",
        low,
    ):
        return False
    return bool(re.search(r"map|perimeter|situation", low))


def _bytes_look_like_map_image(data: bytes) -> bool:
    """Reject tiny/square agency badges mistaken for maps."""
    try:
        from io import BytesIO

        from PIL import Image

        image = Image.open(BytesIO(data))
        w, h = image.size
    except Exception:
        return len(data) >= 20_000
    if w < 280 or h < 200:
        return False
    # Logos are usually ~square; incident maps are wider landscape crops.
    if w <= 400 and h <= 400 and abs(w - h) < 40:
        return False
    return True


def _extract_map_image_urls(html: str, base_url: str) -> list[str]:
    found: list[str] = []
    for raw in re.findall(r'(?:src|href)=["\']([^"\']+)["\']', html, flags=re.I):
        href = unescape(raw.strip())
        if not href or href.startswith("#"):
            continue
        abs_url = urljoin(base_url, href)
        if _looks_like_map_image(abs_url):
            found.append(abs_url)
    # Dedupe preserve order
    out: list[str] = []
    seen: set[str] = set()
    for u in found:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _bbox_around(lat: float, lon: float, *, span_deg: float = 0.35) -> tuple[float, float, float, float]:
    half = max(0.08, float(span_deg) / 2.0)
    return lon - half, lat - half, lon + half, lat + half


def _download_bytes(url: str, *, timeout: float = PAGE_TIMEOUT_S) -> bytes | None:
    try:
        resp = _session().get(url, timeout=timeout)
        resp.raise_for_status()
        if not resp.content or len(resp.content) < 64:
            return None
        return resp.content
    except Exception as exc:
        logger.debug("CAL FIRE map download failed %s: %s", url, exc)
        return None


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
        logger.warning("Could not cache CAL FIRE map %s: %s", path, exc)
        return None


def _topo_export_map(lat: float, lon: float, fire_id: str) -> str | None:
    """Static topo snapshot similar to the Esri map on CAL FIRE incident pages."""
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
        logger.debug("Topo export failed: %s", exc)
        return None
    if not data or len(data) < 200:
        return None
    # Mark the incident with the same fire icon used on the radar (not a red dot).
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
        # Anchor near the flame base so the tip reads above the point.
        paste_x = int(x - icon_w // 2)
        paste_y = int(y - int(icon_h * 0.72))
        image.alpha_composite(icon, (paste_x, paste_y))
        buf = BytesIO()
        image.save(buf, format="PNG")
        data = buf.getvalue()
    except Exception:
        logger.debug("Could not overlay fire icon on topo map", exc_info=True)
    return _write_map_file(fire_id, data, suffix=".png")


def fetch_map_for_fire(fire: dict[str, Any]) -> str | None:
    """Download a map image for a CAL FIRE incident when available; else topo snapshot."""
    fire_id = str(fire.get("id") or "").strip()
    if not fire_id:
        return None
    lat = _as_float(fire.get("lat"))
    lon = _as_float(fire.get("lon"))
    page_url = _incident_page_url(fire.get("url"))
    html = ""
    if page_url:
        try:
            resp = _session().get(page_url, timeout=PAGE_TIMEOUT_S)
            if resp.ok:
                html = resp.text or ""
        except Exception as exc:
            logger.debug("CAL FIRE incident page failed: %s", exc)

    if html:
        for img_url in _extract_map_image_urls(html, page_url or ""):
            payload = _download_bytes(img_url)
            if not payload or not _bytes_look_like_map_image(payload):
                continue
            ext = ".jpg" if re.search(r"\.jpe?g(\?|$)", img_url, re.I) else ".png"
            path = _write_map_file(fire_id, payload, suffix=ext)
            if path:
                return path

    # Incident pages embed an Esri topo map; synthesize the same style when no still is posted.
    if lat is not None and lon is not None:
        return _topo_export_map(lat, lon, fire_id)
    return None


def request_map(fire: dict[str, Any], on_done=None) -> None:
    """Background-fetch a map image; optional callback(path|None)."""
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
            logger.exception("CAL FIRE map fetch failed for %s", fire_id)
        finally:
            with _lock:
                _map_inflight.discard(fire_id)
                if not path:
                    _map_miss.add(fire_id)
        if on_done:
            on_done(path)

    threading.Thread(target=_work, daemon=True, name="calfire-map").start()
