"""
icao_types.py — ICAO aircraft type designator -> display name lookup.

Uses the same tar1090-db source as FlightScnr (tools/icao_types_to_header.py):
https://github.com/yashmulgaonkar/FlightScnr

Run this file directly to download/build: python3 -m utilities.icao_types
"""

from __future__ import annotations

import gzip
import json
import os
import re
from collections import Counter, defaultdict
import requests

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "icao_types.json")
CSV_URL = "https://raw.githubusercontent.com/wiedehopf/tar1090-db/csv/aircraft.csv.gz"

ICAO_TYPE = re.compile(r"^[A-Z0-9]{2,4}$")
MAX_NAME_LEN = 56

_db: dict[str, str] = {}
_loaded = False


def _normalize_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        return name
    parts = name.split(None, 1)
    if len(parts) == 1:
        return parts[0].capitalize()
    return f"{parts[0].capitalize()} {parts[1]}"


def _build_map_from_csv(text: str) -> dict[str, str]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split(";")
        if len(parts) < 5:
            continue
        code = parts[2].strip().upper()
        name = _normalize_name(parts[4].strip())
        if not ICAO_TYPE.match(code) or not name:
            continue
        counts[code][name[:MAX_NAME_LEN]] += 1
    return {
        code: counter.most_common(1)[0][0]
        for code, counter in counts.items()
        if counter
    }


def _download_and_build() -> dict[str, str]:
    print("[ICAO types] Downloading aircraft database...")
    try:
        r = requests.get(CSV_URL, timeout=120)
        r.raise_for_status()
        raw = gzip.decompress(r.content)
        db = _build_map_from_csv(raw.decode("utf-8", errors="replace"))
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f)
        print(f"[ICAO types] Database built — {len(db)} entries cached")
        return db
    except Exception as e:
        print(f"[ICAO types] Download failed: {e}")
        return {}


def _load() -> None:
    global _db, _loaded
    if _loaded:
        return
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                _db = json.load(f)
            _loaded = True
            return
        except Exception:
            pass
    _db = _download_and_build()
    _loaded = True


def get_icao_type_name(code: str) -> str:
    """Look up aircraft display name by ICAO type designator (e.g. B752)."""
    if not code:
        return ""
    _load()
    return _db.get(code.strip().upper(), "")


def format_aircraft_type(code: str) -> str:
    """Return display name for an ICAO type code, or the code if unknown."""
    if not code or code == "—":
        return ""
    code = code.strip().upper()
    name = get_icao_type_name(code)
    return name or code


if __name__ == "__main__":
    _loaded = False
    _db = {}
    built = _download_and_build()
    if built:
        print(f"Sample: B752={built.get('B752')} B738={built.get('B738')}")
