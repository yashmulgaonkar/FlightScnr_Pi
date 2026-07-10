"""
Fetch vessel photos from Wikimedia Commons (free, attribution required).

Looks up by vessel name (and optional IMO). Results are sparse for obscure
ships — famous / named vessels work best. Misses are cached so we don't
hammer the API.
"""

from __future__ import annotations

import hashlib
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

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = (
    "FlightScnrPi/1.0 (https://github.com/yashmulgaonkar/FlightScnr_Pi; "
    "hobby AIS radar; vessel photo enrichment)"
)
SEARCH_TIMEOUT_S = 12
DOWNLOAD_TIMEOUT_S = 20
META_TTL_S = 7 * 24 * 3600  # remember hits/misses for a week
THUMB_WIDTH = 480

_DATA_DIR = os.environ.get("FLIGHTSCNR_DATA_DIR", "/var/lib/flightscnr")
_CACHE_DIR = os.path.join(_DATA_DIR, "vessel_photos")
_META_PATH = os.path.join(_CACHE_DIR, "index.json")

_lock = threading.RLock()
_meta: dict[str, Any] | None = None
_inflight: set[str] = set()


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


def _normalize_name(name: str) -> str:
    text = (name or "").strip().upper()
    text = re.sub(r"\s+", " ", text)
    # Drop common AIS padding / filler
    text = text.replace("@", "").strip()
    return text


def _cache_key(name: str, imo: str = "", mmsi: int | str = "") -> str:
    parts = [_normalize_name(name), str(imo or "").strip(), str(mmsi or "").strip()]
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# Prefer a specific Commons file for a vessel name (File: title without "File:" ok).
_VESSEL_PINNED: dict[str, str] = {
    "MATTHEW TURNER": "Mamma mia! (50437005333).jpg",
    "CAPE HUDSON": (
        "MV Cape Hudson Arrives at Indonesia for Super Garuda Shield 24 Offload (8599448).jpg"
    ),
    "SEA RELIANCE": "Crowley tug-barge pair, Oakland, California.jpg",
}

# Bump when match rules change so stale wrong hits (e.g. animal/people photos) are dropped.
FILTER_VERSION = 5

_MARITIME_TOKENS = (
    "ship", "vessel", "boat", "ferry", "tanker", "cargo", "container",
    "cruise", "yacht", "tug", "barge", "freighter", "bulk", "ro-ro",
    "roro", "lng", "lpg", "trawler", "cutter", "warship", "frigate",
    "destroyer", "carrier", "submarine", "dredger", "pilot", "harbour",
    "harbor", "port of", "imo ", "imo-", "mmsi", "bulk carrier",
    "general cargo", "ro ro", "motor vessel", "sailing vessel",
    "schooner", "brigantine", "sloop", "sailboat", "tall ship",
)

# Strong maritime cues that must appear in the *title* for short/person-like names.
# Matched as whole words so "shipbuilder" does not count as "ship".
_TITLE_MARITIME_TOKENS = (
    "ship", "vessel", "boat", "ferry", "tanker", "cargo", "container",
    "cruise", "yacht", "tug", "barge", "freighter", "trawler", "warship",
    "frigate", "destroyer", "submarine", "dredger", "imo",
    "schooner", "brigantine", "sloop", "sailboat", "tallship", "tall ship",
)

_SKIP_TOKENS = (
    "logo", "flag of", "coat of", "map of", "icon", "diagram", "schematic",
    "drawing", "painting", "sculpture", "statue", "flower", "horse", "dog",
    "cat", "bird", "animal", "plant", "tree", "portrait", "person", "people",
    "building", "church", "castle", "mountain", "landscape", "stamp",
    "coin", "medal", "poster", "advert", "cartoon", "clipart", "svg",
    # People / biography — "Dr. Ray", "Capt. …", "Matthew Turner" collide
    "headshot", "selfie", "biography", "obituary", "memorial", "funeral",
    "wedding", "graduation", "interview", "speaker", "professor", "doctor",
    "physician", "surgeon", "dentist", "nurse", "teacher", "pastor",
    "reverend", "minister", "congressman", "senator", "mayor", "celebrity",
    "actor", "actress", "musician", "singer", "author", "writer",
    "man ", "woman ", "men ", "women ", "boy ", "girl ", "child", "children",
    "crowd", "audience", "family of", "born ", "died ",
    "shipbuilder", "boatbuilder", "shipwright", "naval architect",
    "painter", "photographer", "inventor", "explorer", "chess",
    "tank", "army tank", "armored", "armoured", "howitzer", "artillery",
    "humvee", "bradley", "abrams", "military vehicle",
    # Wildlife / nature — single-word ship names often collide (RACOON, EAGLE, …)
    "raccoon", "racoon", "fox", "bear", "wolf", "deer", "rabbit", "hare",
    "squirrel", "otter", "seal ", "penguin", "dolphin", "whale", "shark",
    "tiger", "lion", "leopard", "panda", "monkey", "ape", "elephant",
    "giraffe", "zebra", "kangaroo", "koala", "owl", "eagle", "hawk",
    "falcon", "parrot", "crow", "raven", "duck", "goose", "swan",
    "wildlife", "zoology", "mammal", "rodent", "insect", "butterfly",
    "spider", "snake", "lizard", "frog", "toad", "aquarium", "zoo ",
)

# Honorific / person-like leading tokens — treat as ambiguous without IMO.
_PERSON_PREFIXES = frozenset({
    "DR", "DOCTOR", "CAPT", "CAPTAIN", "PROF", "PROFESSOR", "REV", "REVEREND",
    "SIR", "LADY", "LORD", "HON", "HONORABLE", "MS", "MRS", "MR",
})

# Names too generic for Commons search — skip photo lookup entirely.
_GENERIC_NAMES = frozenset({
    "SHIP", "VESSEL", "BOAT", "FERRY", "TUG", "BARGE", "YACHT", "UNKNOWN",
    "N/A", "NA", "TEST", "DEMO",
})

# Single-token names that are common animals/objects — never search without IMO.
_AMBIGUOUS_SINGLE_NAMES = frozenset({
    "RACOON", "RACCOON", "EAGLE", "HAWK", "FALCON", "DOLPHIN", "WHALE",
    "SHARK", "TIGER", "LION", "BEAR", "WOLF", "FOX", "DEER", "OTTER",
    "SEAL", "PENGUIN", "OWL", "RAVEN", "CROW", "SWAN", "DUCK", "GOOSE",
    "ROSE", "STAR", "SUN", "MOON", "ORION", "PHOENIX", "DRAGON", "SPIRIT",
    "ANGEL", "QUEEN", "KING", "PRINCE", "PRINCESS", "VIKING", "WARRIOR",
    "HUNTER", "RANGER", "SCOUT", "PIONEER", "VOYAGER", "DISCOVERY",
    "FREEDOM", "LIBERTY", "INDEPENDENCE", "ENTERPRISE", "CHALLENGER",
})


def _name_tokens(name: str) -> list[str]:
    """Significant tokens from a vessel name (drop tiny filler words)."""
    stop = {"THE", "OF", "AND", "A", "AN", "MV", "MS", "MT", "SS", "HMS", "RMS"}
    tokens = []
    for tok in re.findall(r"[A-Z0-9]+", _normalize_name(name)):
        if tok in stop:
            continue
        if len(tok) < 2:
            continue
        tokens.append(tok)
    return tokens


def _has_imo(imo: str = "") -> bool:
    return bool(imo and str(imo).isdigit() and len(str(imo)) >= 7)


def _has_person_honorific(name: str) -> bool:
    """True for Dr./Capt./Prof. style names that almost always hit people photos."""
    tokens = _name_tokens(name)
    if not tokens:
        return False
    return tokens[0] in _PERSON_PREFIXES and len(tokens) <= 3


def _looks_like_person_name(name: str) -> bool:
    """True for honorific names or First Last patterns (Matthew Turner)."""
    tokens = _name_tokens(name)
    if not tokens:
        return False
    if _has_person_honorific(name):
        return True
    # "MATTHEW TURNER", "JOHN SMITH" — two alphabetic name tokens.
    # Still searchable, but title must have a real ship cue (not "shipbuilder").
    if len(tokens) == 2 and all(t.isalpha() and len(t) >= 3 for t in tokens):
        return True
    return False


def _word_in_text(token: str, text: str) -> bool:
    """Whole-word / phrase match so 'ship' does not hit 'shipbuilder'."""
    tok = (token or "").strip().lower()
    if not tok:
        return False
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(tok)}(?![a-z0-9])", text))


def _requires_title_maritime(name: str, *, imo: str = "") -> bool:
    """Short / person-like names need a ship cue in the *title* unless IMO pins it."""
    if _has_imo(imo):
        return False
    tokens = _name_tokens(name)
    if len(tokens) <= 2:
        return True
    if _looks_like_person_name(name):
        return True
    return False


def _name_is_searchable(name: str, *, imo: str = "") -> bool:
    clean = _normalize_name(name)
    if not clean or clean in _GENERIC_NAMES:
        return False
    tokens = _name_tokens(clean)
    if not tokens:
        return False
    if len(clean) < 4:
        return False
    # Single short token ("STAR", "ROSE") is too ambiguous
    if len(tokens) == 1 and len(tokens[0]) < 5:
        return False
    # Single-word animal/common names: only searchable when IMO pins the vessel.
    if len(tokens) == 1 and tokens[0] in _AMBIGUOUS_SINGLE_NAMES and not _has_imo(imo):
        return False
    # Any other single-token name without IMO is too easy to confuse with
    # wildlife / landmarks / logos on Commons.
    if len(tokens) == 1 and not _has_imo(imo):
        return False
    # "Dr. Ray" / "Capt. Smith" without IMO → people photos dominate Commons.
    # First+Last names (Matthew Turner) stay searchable but need a title ship cue.
    if _has_person_honorific(clean) and not _has_imo(imo):
        return False
    return True


def _search_queries(name: str, imo: str = "") -> list[str]:
    clean = _normalize_name(name)
    queries: list[str] = []
    has_imo = bool(imo and str(imo).isdigit() and len(str(imo)) >= 7)
    if has_imo:
        queries.append(f"filetype:bitmap IMO {imo}")
        queries.append(f"filetype:bitmap IMO{imo}")
    if _name_is_searchable(clean, imo=str(imo or "")):
        # Always require a maritime qualifier — never bare name search.
        queries.append(f'filetype:bitmap "{clean}" ship')
        queries.append(f'filetype:bitmap "{clean}" vessel')
        if has_imo:
            queries.append(f'filetype:bitmap "{clean}" IMO {imo}')
        if len(clean) >= 8 and len(_name_tokens(clean)) >= 2:
            queries.append(f"filetype:bitmap {clean} ship")
    # Dedupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


def _haystack_for_page(page: dict, info: dict) -> str:
    ext = info.get("extmetadata") or {}
    parts = [
        page.get("title") or "",
        _ext_text(ext, "ObjectName"),
        _ext_text(ext, "ImageDescription"),
        _ext_text(ext, "Credit"),
    ]
    return _strip_html(" ".join(parts)).lower()


def _name_matches_haystack(name: str, haystack: str) -> bool:
    """Require vessel name tokens to appear in title/description."""
    tokens = _name_tokens(name)
    if not tokens:
        return False
    # All significant tokens must appear (handles "QUEEN MARY 2")
    hits = sum(1 for tok in tokens if tok.lower() in haystack)
    if len(tokens) == 1:
        return hits == 1
    # Allow one miss for long multi-word names, but require majority
    return hits >= max(2, (len(tokens) + 1) // 2) and hits >= len(tokens) - 1


def _has_maritime_context(haystack: str, imo: str = "") -> bool:
    if imo and str(imo).isdigit() and str(imo) in haystack.replace(" ", ""):
        return True
    return any(_word_in_text(tok, haystack) for tok in _MARITIME_TOKENS)


def _title_has_maritime_cue(title: str) -> bool:
    t = (title or "").lower()
    # Strip "File:" prefix noise
    if t.startswith("file:"):
        t = t[5:]
    return any(_word_in_text(tok, t) for tok in _TITLE_MARITIME_TOKENS)


def _looks_like_non_vessel(haystack: str, *, name: str = "") -> bool:
    """True if metadata looks like wildlife/people/art rather than a vessel photo."""
    name_tokens = {t.lower() for t in _name_tokens(name)}
    # Spelling variants so a ship named RACOON isn't blocked by "raccoon" in SKIP.
    variants = set(name_tokens)
    if "racoon" in variants:
        variants.add("raccoon")
    if "raccoon" in variants:
        variants.add("racoon")

    for tok in _SKIP_TOKENS:
        tok_key = tok.strip().lower()
        if not tok_key:
            continue
        if tok_key in variants:
            continue
        # Word-boundary match — avoid "otter" inside "Rotterdam".
        if not re.search(rf"(?<![a-z0-9]){re.escape(tok_key)}(?![a-z0-9])", haystack):
            continue
        return True
    return False


# Back-compat alias used by older tests / call sites.
_looks_like_wildlife = _looks_like_non_vessel


def _cache_entry_still_valid(entry: dict, *, name: str = "", imo: str = "") -> bool:
    """Reject stale cache rows from looser filter versions / wildlife hits."""
    if int(entry.get("filter_version") or 0) < FILTER_VERSION:
        return False
    clean = _normalize_name(name)
    pinned = _VESSEL_PINNED.get(clean)
    if pinned:
        title = (entry.get("title") or "").replace("File:", "").strip()
        return title == pinned.strip()
    title = (entry.get("title") or "").lower()
    if title and _looks_like_non_vessel(title, name=name):
        return False
    if title and not _has_maritime_context(title, imo) and not (
        imo and str(imo) in title.replace(" ", "")
    ):
        return False
    if _requires_title_maritime(name, imo=imo) and title and not _title_has_maritime_cue(title):
        if not (imo and str(imo) in title.replace(" ", "")):
            return False
    return True


def _commons_file_title(name: str) -> str:
    title = (name or "").strip().replace("_", " ")
    if title.lower().startswith("file:"):
        return "File:" + title[5:].lstrip()
    return f"File:{title}"


def _fetch_commons_file(file_title: str) -> dict | None:
    """Load a specific Commons File: page (for vessel pins)."""
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
    headers = {"User-Agent": USER_AGENT}
    url = f"{COMMONS_API}?{urlencode(params)}"
    resp = requests.get(url, headers=headers, timeout=SEARCH_TIMEOUT_S)
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


def _pick_best_page(pages: dict, *, name: str = "", imo: str = "") -> dict | None:
    """Accept only photos that match the vessel name and look maritime."""
    candidates: list[tuple[float, dict, dict]] = []
    need_title_cue = _requires_title_maritime(name, imo=imo)
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
        if _looks_like_non_vessel(haystack, name=name):
            continue
        w = int(info.get("width") or 0)
        h = int(info.get("height") or 0)
        if w < 320 or h < 180:
            continue

        # Hard filters: name match + maritime context (unless IMO is in metadata)
        imo_hit = bool(imo) and str(imo) in haystack.replace(" ", "")
        if name and not imo_hit and not _name_matches_haystack(name, haystack):
            continue
        if not _has_maritime_context(haystack, imo):
            continue
        # Short / person-like names: demand a maritime cue in the *title* so a
        # portrait that merely mentions "harbor" in a caption cannot win.
        if need_title_cue and not imo_hit and not _title_has_maritime_cue(title):
            continue

        score = float(min(w, 2000))
        if w >= h:
            score += 200
        if any(_word_in_text(tok, haystack) for tok in ("ship", "vessel", "boat", "ferry", "tanker", "cargo", "schooner")):
            score += 200
        if _title_has_maritime_cue(title):
            score += 250
        if imo_hit:
            score += 500
        # Prefer title that contains the full normalized name
        clean = _normalize_name(name).lower()
        if clean and clean in haystack:
            score += 300
        candidates.append((score, page, info))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    page, info = candidates[0][1], candidates[0][2]
    return {"page": page, "info": info}


def _search_commons(query: str, *, name: str = "", imo: str = "") -> dict | None:
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
    headers = {"User-Agent": USER_AGENT}
    url = f"{COMMONS_API}?{urlencode(params)}"
    resp = requests.get(url, headers=headers, timeout=SEARCH_TIMEOUT_S)
    resp.raise_for_status()
    data = resp.json()
    pages = (data.get("query") or {}).get("pages") or {}
    return _pick_best_page(pages, name=name, imo=imo)


def _ext_text(meta: dict, key: str) -> str:
    block = meta.get(key) or {}
    if isinstance(block, dict):
        return (block.get("value") or "").strip()
    return str(block or "").strip()


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _download_thumb(url: str, dest_path: str) -> bool:
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(url, headers=headers, timeout=DOWNLOAD_TIMEOUT_S, stream=True)
    resp.raise_for_status()
    tmp = dest_path + ".tmp"
    with open(tmp, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                fh.write(chunk)
    os.replace(tmp, dest_path)
    return os.path.isfile(dest_path) and os.path.getsize(dest_path) > 100


def lookup_vessel_photo(
    *,
    name: str = "",
    imo: str = "",
    mmsi: int | str = "",
    force: bool = False,
) -> dict | None:
    """
    Return photo metadata + local path, or None on miss.

    Keys: path, thumb_url, page_url, title, license, artist, source, cached
    """
    clean = _normalize_name(name)
    if not clean and not imo:
        return None
    pinned = _VESSEL_PINNED.get(clean) if clean else None
    # Pinned vessels bypass the ambiguous-name skip.
    if not pinned and clean and not _name_is_searchable(clean, imo=str(imo or "")) and not (
        imo and str(imo).isdigit() and len(str(imo)) >= 7
    ):
        logger.info("[commons] skip ambiguous name %r", clean)
        return None

    key = _cache_key(clean, imo, mmsi)
    meta = _load_meta()
    now = time.time()

    with _lock:
        entry = meta.get(key)
        if entry and not force:
            if now - float(entry.get("ts") or 0) < META_TTL_S:
                if entry.get("miss"):
                    return None
                path = entry.get("path") or ""
                if path and os.path.isfile(path):
                    if _cache_entry_still_valid(entry, name=clean, imo=str(imo or "")):
                        out = dict(entry)
                        out["cached"] = True
                        return out
                    logger.info(
                        "[commons] dropping stale/weak cache for %r (%s)",
                        clean,
                        entry.get("title") or path,
                    )
                # File missing or invalid — fall through to re-fetch

    chosen = None
    used_query = ""
    if pinned:
        try:
            logger.info("[commons] vessel pin %r → %s", clean, pinned)
            chosen = _fetch_commons_file(pinned)
            used_query = f"pin:{pinned}"
        except requests.RequestException as exc:
            logger.warning("[commons] pin fetch failed: %s", exc)
            chosen = None

    if not chosen:
        queries = _search_queries(clean, imo)
        if not queries and not pinned:
            logger.info("[commons] skip ambiguous name %r (no safe queries)", clean)
            return None
        for query in queries:
            try:
                logger.info("[commons] search %r", query)
                hit = _search_commons(query, name=clean, imo=str(imo or ""))
            except requests.RequestException as exc:
                logger.warning("[commons] search failed: %s", exc)
                continue
            if hit:
                chosen = hit
                used_query = query
                break

    if not chosen:
        with _lock:
            meta[key] = {"miss": True, "ts": now, "name": clean, "imo": imo}
            _save_meta()
        logger.info("[commons] no photo for %r (imo=%s)", clean, imo or "-")
        return None
    info = chosen["info"]
    page = chosen["page"]
    thumb = info.get("thumburl") or info.get("url")
    if not thumb:
        with _lock:
            meta[key] = {"miss": True, "ts": now, "name": clean}
            _save_meta()
        return None

    extmeta = info.get("extmetadata") or {}
    license_name = _strip_html(_ext_text(extmeta, "LicenseShortName")) or "Commons"
    artist = _strip_html(_ext_text(extmeta, "Artist"))
    title = (page.get("title") or "").replace("File:", "")
    page_url = f"https://commons.wikimedia.org/wiki/{page.get('title', '').replace(' ', '_')}"

    _ensure_cache_dir()
    ext = ".jpg"
    mime = (info.get("mime") or "").lower()
    if "png" in mime:
        ext = ".png"
    elif "webp" in mime:
        ext = ".webp"
    dest = os.path.join(_CACHE_DIR, f"{key}{ext}")

    try:
        ok = _download_thumb(thumb, dest)
    except requests.RequestException as exc:
        logger.warning("[commons] download failed: %s", exc)
        ok = False
    if not ok:
        return None

    result = {
        "miss": False,
        "ts": now,
        "name": clean,
        "imo": imo,
        "mmsi": str(mmsi or ""),
        "path": dest,
        "thumb_url": thumb,
        "page_url": page_url,
        "title": title,
        "license": license_name,
        "artist": artist,
        "query": used_query,
        "source": "wikimedia_commons",
        "filter_version": FILTER_VERSION,
        "cached": False,
    }
    with _lock:
        meta[key] = {k: v for k, v in result.items() if k != "cached"}
        _save_meta()
    logger.info(
        "[commons] photo for %r → %s (%s)",
        clean,
        title[:60],
        license_name,
    )
    return result


def clear_vessel_photo_cache() -> int:
    """Delete cached photos + index. Returns number of files removed."""
    global _meta
    removed = 0
    with _lock:
        _ensure_cache_dir()
        try:
            for name in os.listdir(_CACHE_DIR):
                path = os.path.join(_CACHE_DIR, name)
                if os.path.isfile(path):
                    try:
                        os.remove(path)
                        removed += 1
                    except OSError:
                        pass
        except OSError:
            pass
        _meta = {}
        _save_meta()
    logger.info("[commons] cleared vessel photo cache (%d files)", removed)
    return removed


def get_cached_vessel_photo(name: str = "", imo: str = "", mmsi: int | str = "") -> dict | None:
    """Return a previously cached hit without network I/O."""
    key = _cache_key(name, imo, mmsi)
    meta = _load_meta()
    with _lock:
        entry = meta.get(key)
        if not entry or entry.get("miss"):
            return None
        if not _cache_entry_still_valid(entry, name=name, imo=str(imo or "")):
            return None
        path = entry.get("path") or ""
        if path and os.path.isfile(path):
            out = dict(entry)
            out["cached"] = True
            return out
    return None


def vessel_photo_cache_key(vessel: dict) -> str:
    return _cache_key(
        vessel.get("name") or vessel.get("callsign") or "",
        vessel.get("imo") or "",
        vessel.get("mmsi") or "",
    )


def fetch_vessel_photo_for(vessel: dict, *, force: bool = False) -> dict | None:
    """Convenience wrapper for radar/detail vessel dicts (Wikimedia Commons)."""
    if not vessel or vessel.get("kind") != "vessel":
        return None
    name = (vessel.get("name") or vessel.get("callsign") or "").strip()
    # Skip generic MMSI-only labels — Commons won't match
    if name.upper().startswith("MMSI "):
        name = ""
    return lookup_vessel_photo(
        name=name,
        imo=str(vessel.get("imo") or ""),
        mmsi=vessel.get("mmsi") or "",
        force=force,
    )
