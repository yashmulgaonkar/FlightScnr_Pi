"""
cities.py — Nearest city lookup using GeoNames cities5000 dataset.

Downloads cities5000.zip from GeoNames on first run and caches as
cities.json in the project root. Subsequent lookups are instant.

Source: https://download.geonames.org/export/dump/cities5000.zip
No API key required. ~50K cities with population > 5000.

Usage:
    from utilities.cities import get_nearest_city
    nearest = get_nearest_city(40.6413, -73.7781)
    # {"name": "New York City", "distance_km": 18.3}
"""

import json
import math
import os
import threading
import zipfile
from io import BytesIO

import requests

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "cities.json")
ZIP_URL = "https://download.geonames.org/export/dump/cities5000.zip"

CACHE_VERSION = 1

# In-memory list: [[name, lat, lon], ...]
_db = []
_loaded = False
_load_lock = threading.Lock()


def _haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km between two points."""
    lat1, lon1 = math.radians(lat1), math.radians(lon1)
    lat2, lon2 = math.radians(lat2), math.radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _download_and_build():
    """Download cities5000.zip, extract, and build city list."""
    print("[Cities] Downloading cities5000 database...")
    try:
        r = requests.get(ZIP_URL, timeout=(10, 60))
        r.raise_for_status()

        cities = []
        with zipfile.ZipFile(BytesIO(r.content)) as zf:
            with zf.open("cities5000.txt") as f:
                for line in f:
                    fields = line.decode("utf-8").split("\t")
                    if len(fields) < 6:
                        continue
                    name = fields[2] or fields[1]  # asciiname preferred (bitmap font safe)
                    try:
                        lat = float(fields[4])
                        lon = float(fields[5])
                    except (ValueError, IndexError):
                        continue
                    cities.append([name, lat, lon])

        cache_data = {"_version": CACHE_VERSION, "cities": cities}
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f)
        print(f"[Cities] Database built — {len(cities)} cities cached to cities.json (v{CACHE_VERSION})")
        return cities

    except Exception as e:
        print(f"[Cities] Download failed: {e}")
        return []


def _load():
    """Load from cache file or download if not present. Thread-safe."""
    global _db, _loaded
    if _loaded:
        return

    with _load_lock:
        if _loaded:  # double-check after acquiring lock
            return

        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)

                if isinstance(raw, dict) and raw.get("_version") == CACHE_VERSION:
                    _db = raw.get("cities", [])
                    _loaded = True
                    return
                else:
                    version_found = raw.get("_version", "none") if isinstance(raw, dict) else "legacy"
                    print(f"[Cities] Cache version mismatch (found: {version_found}, need: {CACHE_VERSION}) — rebuilding")
            except Exception as e:
                print(f"[Cities] Cache load failed: {e} — re-downloading")

        _db = _download_and_build()
        _loaded = True


def get_nearest_city(latitude, longitude):
    """
    Find the nearest city to the given coordinates.

    Returns {"name": str, "distance_km": float} or None if no cities loaded.
    """
    _load()
    if not _db:
        return None

    best_name = None
    best_dist = float("inf")

    for name, clat, clon in _db:
        dist = _haversine_km(latitude, longitude, clat, clon)
        if dist < best_dist:
            best_dist = dist
            best_name = name

    if best_name is None:
        return None

    return {"name": best_name, "distance_km": best_dist}


def refresh():
    """Force re-download of cities database."""
    global _db, _loaded
    _loaded = False
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    _load()
