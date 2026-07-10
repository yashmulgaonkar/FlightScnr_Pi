"""
Aircraft photos via planespotters.net (hex/reg), with Wikimedia Commons
type-model fallback when the specific airframe has no photo.

Planespotters (free, non-commercial, attribution):
  GET https://api.planespotters.net/pub/photos/hex/{icao}
  GET https://api.planespotters.net/pub/photos/reg/{registration}

Commons fallback searches by ICAO type designator display name
(e.g. EC45 → Airbus EC-145 / Eurocopter EC145).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

PLANESPOTTERS_UA = (
    "FlightScnrPi/1.0 (+https://github.com/yashmulgaonkar/FlightScnr_Pi)"
)
COMMONS_UA = (
    "FlightScnrPi/1.0 (https://github.com/yashmulgaonkar/FlightScnr_Pi; "
    "hobby radar; aircraft type photo fallback)"
)
API_HEX = "https://api.planespotters.net/pub/photos/hex"
API_REG = "https://api.planespotters.net/pub/photos/reg"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
SEARCH_TIMEOUT_S = 8
DOWNLOAD_TIMEOUT_S = 12
META_TTL_S = 14 * 24 * 3600  # hits/misses remembered two weeks
THUMB_WIDTH = 480
# Bump when lookup order / type fallback rules change so stale misses retry.
PHOTO_LOGIC_VERSION = 4

_DATA_DIR = os.environ.get("FLIGHTSCNR_DATA_DIR", "/var/lib/flightscnr")
_CACHE_DIR = os.path.join(_DATA_DIR, "aircraft_photos")
_META_PATH = os.path.join(_CACHE_DIR, "index.json")

_lock = threading.RLock()
_meta: dict[str, Any] | None = None

# Extra Commons search aliases when ICAO DB names are awkward.
_TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "EC45": ("Eurocopter EC145", "Airbus H145", "EC-145 helicopter"),
    "EC35": ("Eurocopter EC135", "Airbus H135", "EC-135 helicopter"),
    "AS65": ("Aerospatiale AS365", "AS365 Dauphin", "SA365 Dauphin"),
    "A21N": ("Airbus A321neo", "Airbus A321-251N"),
    "B38M": ("Boeing 737 MAX 8", "Boeing 737-8"),
    "UH72": ("UH-72 Lakota", "Eurocopter EC145 Army"),
}

# Prefer a specific Commons file for a type (File: title without "File:" ok).
_TYPE_PINNED: dict[str, str] = {
    "C172": (
        "Cessna 172S Skyhawk SP (N1419D, cn 172S10671) (10-19-2022).jpg"
    ),
    "C152": "Cessna 152 Aeroandes (5129490525).jpg",
    "S22T": "Cirrus SR22T (17159845664).jpg",
    "SR22": "Cirrus SR22T (17159845664).jpg",
    "BE33": "N9520Q Beech 35-C33A Debonair s n CE-21 (54312024456).jpg",
    "C82S": "N61907 Cessna T182T Turbo Skylane TC s n T18208861 (54625681905).jpg",
    "AS65": (
        "Aerospatiale MH-65D Dolphin ‘6519’ (27371268621).jpg"
    ),
}


def _ensure_cache_dir() -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)


def _load_meta() -> dict[str, Any]:
    global _meta
    with _lock:
        if _meta is not None:
            return _meta
        _ensure_cache_dir()
        try:
            with open(_META_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
            _meta = data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError, TypeError):
            _meta = {}
        return _meta


def _save_meta() -> None:
    with _lock:
        _ensure_cache_dir()
        tmp = _META_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_meta or {}, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, _META_PATH)


def normalize_icao_hex(value) -> str:
    """Return a 6-char lowercase ICAO hex, or empty string."""
    if value is None:
        return ""
    hex_id = re.sub(r"[^0-9a-fA-F]", "", str(value).strip())
    if len(hex_id) < 6:
        return ""
    return hex_id[-6:].lower()


def normalize_type_code(value) -> str:
    """Return a short ICAO type designator (e.g. EC45), or empty."""
    if value is None:
        return ""
    code = re.sub(r"[^A-Za-z0-9]", "", str(value).strip()).upper()
    if 2 <= len(code) <= 4:
        return code
    return ""


def normalize_registration(value) -> str:
    if value is None:
        return ""
    reg = re.sub(r"\s+", "", str(value).strip().upper())
    # Keep hyphens used in military / some civil regs (12-72233).
    reg = re.sub(r"[^A-Z0-9\-]", "", reg)
    if len(reg) < 3:
        return ""
    return reg


def _headers() -> dict[str, str]:
    return {"User-Agent": PLANESPOTTERS_UA, "Accept": "application/json"}


def _download(url: str, dest_path: str, *, user_agent: str = PLANESPOTTERS_UA) -> bool:
    resp = requests.get(
        url,
        headers={"User-Agent": user_agent},
        timeout=DOWNLOAD_TIMEOUT_S,
        stream=True,
    )
    resp.raise_for_status()
    tmp = dest_path + ".tmp"
    with open(tmp, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                fh.write(chunk)
    os.replace(tmp, dest_path)
    return os.path.isfile(dest_path) and os.path.getsize(dest_path) > 100


def _pick_image_url(photo: dict) -> str:
    """Prefer a reasonably sized thumbnail."""
    for key in ("thumbnail_large", "thumbnail", "thumbnail_large_src"):
        block = photo.get(key)
        if isinstance(block, dict):
            src = (block.get("src") or "").strip()
            if src:
                return src
        elif isinstance(block, str) and block.strip():
            return block.strip()
    link = photo.get("link")
    if isinstance(link, str) and link.startswith("http"):
        return link
    return ""


def _cache_entry_usable(entry: dict) -> bool:
    if int(entry.get("logic_version") or 0) < PHOTO_LOGIC_VERSION:
        return False
    # Type pins: drop cached type photos that aren't the pinned file.
    if entry.get("match") == "type":
        code = normalize_type_code(entry.get("type_code") or "")
        pinned = _TYPE_PINNED.get(code)
        if pinned:
            title = (entry.get("title") or "").replace("File:", "").strip()
            if title != pinned.strip():
                return False
    return True


def _commons_file_title(name: str) -> str:
    title = (name or "").strip().replace("_", " ")
    if title.lower().startswith("file:"):
        return "File:" + title[5:].lstrip()
    return f"File:{title}"


def _fetch_commons_file(file_title: str) -> dict | None:
    """Load a specific Commons File: page (for type pins)."""
    title = _commons_file_title(file_title)
    params = {
        "action": "query",
        "format": "json",
        "titles": title,
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": THUMB_WIDTH,
        "iiextmetadatafilter": "LicenseShortName|Artist|Credit|ImageDescription|ObjectName",
    }
    url = f"{COMMONS_API}?{urlencode(params)}"
    resp = requests.get(
        url, headers={"User-Agent": COMMONS_UA}, timeout=SEARCH_TIMEOUT_S
    )
    resp.raise_for_status()
    data = resp.json()
    pages = (data.get("query") or {}).get("pages") or {}
    for page in pages.values():
        if page.get("missing") is not None:
            continue
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        return {"page": page, "info": infos[0]}
    return None


def _result_from_commons_hit(
    chosen: dict,
    *,
    hex_id: str,
    type_code: str,
    now: float,
    used_query: str,
) -> dict | None:
    info = chosen["info"]
    page = chosen["page"]
    thumb = info.get("thumburl") or info.get("url")
    if not thumb:
        return None

    extmeta = info.get("extmetadata") or {}
    license_name = _strip_html(_ext_text(extmeta, "LicenseShortName")) or "Commons"
    artist = _strip_html(_ext_text(extmeta, "Artist"))
    title = (page.get("title") or "").replace("File:", "")
    page_url = (
        "https://commons.wikimedia.org/wiki/"
        + (page.get("title") or "").replace(" ", "_")
    )

    _ensure_cache_dir()
    ext = ".jpg"
    mime = (info.get("mime") or "").lower()
    if "png" in mime:
        ext = ".png"
    elif "webp" in mime:
        ext = ".webp"
    # Shared file for a type so every C172 hex reuses one download.
    dest = os.path.join(_CACHE_DIR, f"type_{type_code.lower()}{ext}")
    marker = dest + ".title"

    try:
        reuse = (
            os.path.isfile(dest)
            and os.path.getsize(dest) > 100
            and os.path.isfile(marker)
            and open(marker, encoding="utf-8").read().strip() == title
        )
        if reuse:
            ok = True
        else:
            ok = _download(thumb, dest, user_agent=COMMONS_UA)
            if ok:
                with open(marker, "w", encoding="utf-8") as fh:
                    fh.write(title + "\n")
    except (requests.RequestException, OSError) as exc:
        logger.warning("[photo] commons download failed: %s", exc)
        ok = False
    if not ok:
        return None

    result = {
        "miss": False,
        "ts": now,
        "hex": hex_id,
        "path": dest,
        "photographer": artist,
        "page_url": page_url,
        "thumb_url": thumb,
        "source": "wikimedia_commons",
        "match": "type",
        "type_code": type_code,
        "title": title,
        "license": license_name,
        "query": used_query,
        "logic_version": PHOTO_LOGIC_VERSION,
        "cached": False,
    }
    meta = _load_meta()
    with _lock:
        meta[hex_id] = {k: v for k, v in result.items() if k != "cached"}
        _save_meta()
    logger.info(
        "[photo] %s: type fallback %s → %s",
        hex_id,
        type_code,
        title[:60],
    )
    return result


def _lookup_type_commons(type_code: str, hex_id: str, now: float) -> dict | None:
    code = normalize_type_code(type_code)
    if not code:
        return None

    pinned = _TYPE_PINNED.get(code)
    if pinned:
        try:
            logger.info("[photo] commons type pin %s → %s", code, pinned)
            hit = _fetch_commons_file(pinned)
        except (requests.RequestException, ValueError, TypeError) as exc:
            logger.warning("[photo] commons pin fetch failed: %s", exc)
            hit = None
        if hit:
            return _result_from_commons_hit(
                hit,
                hex_id=hex_id,
                type_code=code,
                now=now,
                used_query=f"pin:{pinned}",
            )

    queries = _type_search_queries(code)
    if not queries:
        return None

    chosen = None
    used_query = ""
    for query in queries:
        try:
            logger.info("[photo] commons type search %s %r", code, query)
            hit = _search_commons_type(query, type_code=code)
        except (requests.RequestException, ValueError, TypeError) as exc:
            logger.warning("[photo] commons type search failed: %s", exc)
            continue
        if hit:
            chosen = hit
            used_query = query
            break
    if not chosen:
        return None

    return _result_from_commons_hit(
        chosen,
        hex_id=hex_id,
        type_code=code,
        now=now,
        used_query=used_query,
    )


def _store_miss(hex_id: str, now: float) -> None:
    meta = _load_meta()
    with _lock:
        meta[hex_id] = {
            "miss": True,
            "ts": now,
            "hex": hex_id,
            "logic_version": PHOTO_LOGIC_VERSION,
        }
        _save_meta()


def _planespotters_lookup(url: str) -> dict | None:
    """Return first photo dict from planespotters, or None."""
    try:
        resp = requests.get(url, headers=_headers(), timeout=SEARCH_TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError, TypeError) as exc:
        logger.warning("[photo] planespotters request failed: %s", exc)
        return None
    photos = data.get("photos") if isinstance(data, dict) else None
    if not photos:
        return None
    photo = photos[0] if isinstance(photos[0], dict) else {}
    return photo or None


def _download_planespotters_image(img_url: str, dest: str) -> bool:
    bare = img_url
    if bare.startswith("https://"):
        bare = bare[8:]
    elif bare.startswith("http://"):
        bare = bare[7:]
    proxied = (
        f"https://images.weserv.nl/?url={bare}"
        f"&w={THUMB_WIDTH}&fit=inside&output=jpg"
    )
    for candidate in (proxied, img_url):
        try:
            if _download(candidate, dest):
                return True
        except requests.RequestException as exc:
            logger.debug("[photo] download via %s failed: %s", candidate[:48], exc)
    return False


def _type_display_name(type_code: str) -> str:
    try:
        from utilities.icao_types import get_icao_type_name

        return (get_icao_type_name(type_code) or "").strip()
    except Exception:
        return ""


def _type_search_queries(type_code: str) -> list[str]:
    """Build Commons search queries for an ICAO type designator."""
    code = normalize_type_code(type_code)
    if not code:
        return []

    queries: list[str] = []
    name = _type_display_name(code)
    # Soften awkward DB names: "Airbus HELICOPTERS EC-145" → usable phrases
    if name:
        cleaned = re.sub(r"\bHELICOPTERS?\b", "", name, flags=re.I)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -/")
        if cleaned:
            queries.append(f"filetype:bitmap {cleaned}")
            # Also try without manufacturer prefix for tighter matches
            parts = cleaned.split(None, 1)
            if len(parts) == 2 and len(parts[1]) >= 4:
                queries.append(f"filetype:bitmap {parts[1]} aircraft")
                queries.append(f"filetype:bitmap {parts[1]} helicopter")

    for alias in _TYPE_ALIASES.get(code, ()):
        queries.append(f"filetype:bitmap {alias}")

    # Last resort: bare code (often weak — keep last)
    if name:
        queries.append(f"filetype:bitmap {code} aircraft")

    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = q.lower()
        if key not in seen:
            seen.add(key)
            out.append(q)
    return out[:6]


def _ext_text(meta: dict, key: str) -> str:
    block = meta.get(key) or {}
    if isinstance(block, dict):
        return (block.get("value") or "").strip()
    return str(block or "").strip()


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


_AVIATION_TOKENS = (
    "aircraft", "airplane", "aeroplane", "airliner", "helicopter", "heli",
    "jet", "boeing", "airbus", "eurocopter", "cessna", "piper", "beech",
    "lockheed", "bombardier", "embraer", "atr ", "dash ", "fokker",
    "sikorsky", "bell ", "md ", "ec145", "ec-145", "ec135", "h145", "h135",
    "a321", "a320", "a319", "a330", "a350", "a380", "737", "747", "757",
    "767", "777", "787", "dauphin", "lakota", "as365", "as-365", "sa365",
)

_SKIP_TOKENS = (
    "logo", "flag of", "coat of", "map of", "icon", "diagram", "schematic",
    "drawing", "painting", "sculpture", "stamp", "coin", "medal", "poster",
    "cartoon", "clipart", "svg", "operators.png", "fleet list", "infobox",
    "route map", "airport diagram", "cockpit panel only",
)


def _haystack_for_page(page: dict, info: dict) -> str:
    ext = info.get("extmetadata") or {}
    parts = [
        page.get("title") or "",
        _ext_text(ext, "ObjectName"),
        _ext_text(ext, "ImageDescription"),
        _ext_text(ext, "Credit"),
    ]
    return _strip_html(" ".join(parts)).lower()


def _looks_like_aircraft(haystack: str, type_code: str = "") -> bool:
    if any(tok in haystack for tok in _SKIP_TOKENS):
        return False
    code = (type_code or "").lower()
    if code and code in haystack.replace("-", "").replace(" ", ""):
        return True
    return any(tok in haystack for tok in _AVIATION_TOKENS)


def _pick_commons_page(pages: dict, *, type_code: str = "") -> dict | None:
    candidates: list[tuple[float, dict, dict]] = []
    code = normalize_type_code(type_code)
    name = _type_display_name(code).lower()
    name_bits = [b for b in re.findall(r"[a-z0-9\-]+", name) if len(b) >= 3]
    # Drop ultra-generic manufacturer-only bits from scoring
    name_bits = [b for b in name_bits if b not in ("airbus", "boeing", "helicopters")]

    for page in (pages or {}).values():
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        mime = (info.get("mime") or "").lower()
        if not mime.startswith("image/"):
            continue
        if mime in ("image/svg+xml", "image/gif"):
            continue
        title = (page.get("title") or "")
        haystack = _haystack_for_page(page, info)
        if not _looks_like_aircraft(haystack, code):
            continue
        w = int(info.get("width") or 0)
        h = int(info.get("height") or 0)
        if w < 320 or h < 180:
            continue
        # Prefer landscape photos over maps/charts
        score = float(min(w, 2400))
        if w >= h:
            score += 150
        if "png" in mime and ("operator" in haystack or "map" in haystack):
            score -= 400
        for bit in name_bits:
            if bit in haystack:
                score += 120
        aliases = " ".join(_TYPE_ALIASES.get(code, ())).lower()
        for bit in re.findall(r"[a-z0-9\-]+", aliases):
            if len(bit) >= 4 and bit in haystack:
                score += 80
        candidates.append((score, page, info))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    page, info = candidates[0][1], candidates[0][2]
    return {"page": page, "info": info}


def _search_commons_type(query: str, *, type_code: str = "") -> dict | None:
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrnamespace": 6,
        "gsrlimit": 12,
        "gsrsearch": query,
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": THUMB_WIDTH,
        "iiextmetadatafilter": "LicenseShortName|Artist|Credit|ImageDescription|ObjectName",
    }
    url = f"{COMMONS_API}?{urlencode(params)}"
    resp = requests.get(
        url, headers={"User-Agent": COMMONS_UA}, timeout=SEARCH_TIMEOUT_S
    )
    resp.raise_for_status()
    data = resp.json()
    pages = (data.get("query") or {}).get("pages") or {}
    return _pick_commons_page(pages, type_code=type_code)


def lookup_aircraft_photo(
    icao_hex: str,
    *,
    aircraft_type: str = "",
    registration: str = "",
    force: bool = False,
) -> dict | None:
    """
    Fetch/cache a photo for an ICAO hex.

    Order: planespotters hex → planespotters reg → Commons by ICAO type.
    Returns dict with path, photographer, page_url, source — or None on miss.
    """
    hex_id = normalize_icao_hex(icao_hex)
    if not hex_id:
        return None

    type_code = normalize_type_code(aircraft_type)
    reg = normalize_registration(registration)

    meta = _load_meta()
    now = time.time()
    with _lock:
        entry = meta.get(hex_id)
        if entry and not force:
            if now - float(entry.get("ts") or 0) < META_TTL_S and _cache_entry_usable(entry):
                if entry.get("miss"):
                    # Misses cached before a type pin was added would stick forever;
                    # retry when we now have a pin for this type.
                    if not (type_code and type_code in _TYPE_PINNED):
                        return None
                else:
                    path = entry.get("path") or ""
                    if path and os.path.isfile(path):
                        out = dict(entry)
                        out["cached"] = True
                        return out

    # 1) Planespotters by hex
    logger.info("[photo] planespotters lookup %s", hex_id)
    photo = _planespotters_lookup(f"{API_HEX}/{hex_id}")

    # 2) Planespotters by registration
    if not photo and reg:
        logger.info("[photo] planespotters reg lookup %s", reg)
        photo = _planespotters_lookup(f"{API_REG}/{reg}")

    if photo:
        img_url = _pick_image_url(photo)
        photographer = str(photo.get("photographer") or "").strip()
        page_url = str(
            photo.get("link")
            or f"https://www.planespotters.net/hex/{hex_id.upper()}"
        ).strip()
        if img_url:
            _ensure_cache_dir()
            dest = os.path.join(_CACHE_DIR, f"{hex_id}.jpg")
            if _download_planespotters_image(img_url, dest):
                result = {
                    "miss": False,
                    "ts": now,
                    "hex": hex_id,
                    "path": dest,
                    "photographer": photographer,
                    "page_url": page_url,
                    "thumb_url": img_url,
                    "source": "planespotters",
                    "match": "airframe",
                    "logic_version": PHOTO_LOGIC_VERSION,
                    "cached": False,
                }
                with _lock:
                    meta[hex_id] = {k: v for k, v in result.items() if k != "cached"}
                    _save_meta()
                logger.info(
                    "[photo] %s: cached (%s)",
                    hex_id,
                    photographer or "unknown photographer",
                )
                return result

    # 3) Commons generic type photo
    if type_code:
        commons = _lookup_type_commons(type_code, hex_id, now)
        if commons:
            return commons

    _store_miss(hex_id, now)
    logger.info("[photo] %s: no photo available", hex_id)
    return None


def get_cached_aircraft_photo(icao_hex: str) -> dict | None:
    hex_id = normalize_icao_hex(icao_hex)
    if not hex_id:
        return None
    meta = _load_meta()
    with _lock:
        entry = meta.get(hex_id)
        if not entry or entry.get("miss"):
            return None
        if not _cache_entry_usable(entry):
            return None
        path = entry.get("path") or ""
        if path and os.path.isfile(path):
            out = dict(entry)
            out["cached"] = True
            return out
    return None


def fetch_aircraft_photo_for(flight: dict, *, force: bool = False) -> dict | None:
    if not flight:
        return None
    hex_id = normalize_icao_hex(flight.get("icao_hex") or flight.get("hex"))
    if not hex_id:
        return None
    aircraft_type = (
        flight.get("plane")
        or flight.get("aircraft_type")
        or flight.get("aircraft_code")
        or ""
    )
    registration = (
        flight.get("registration")
        or flight.get("reg")
        or flight.get("tail")
        or ""
    )
    return lookup_aircraft_photo(
        hex_id,
        aircraft_type=str(aircraft_type or ""),
        registration=str(registration or ""),
        force=force,
    )


def photo_credit_line(photo: dict | None) -> str:
    if not photo:
        return ""
    source = (photo.get("source") or "").strip()
    if source == "wikimedia_commons":
        artist = (photo.get("photographer") or photo.get("artist") or "").strip()
        license_name = (photo.get("license") or "").strip()
        if artist:
            line = f"© {artist}"
        elif license_name:
            line = f"{license_name} · Commons"
        else:
            line = "Wikimedia Commons"
        if photo.get("match") == "type":
            code = (photo.get("type_code") or "").strip()
            if code and len(line) < 28:
                line = f"{line} · {code}"
    else:
        photographer = (photo.get("photographer") or "").strip()
        if photographer:
            line = f"© {photographer}"
        else:
            line = "planespotters.net"
    if len(line) > 40:
        line = line[:37] + "…"
    return line
