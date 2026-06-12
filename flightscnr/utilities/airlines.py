"""
airlines.py — Airline name lookup from local database.
Downloads airlines.json from GitHub on first run, caches as airlines.json.
Run this file directly to download: python3 airlines.py

Source: https://github.com/npow/airline-codes
"""

import json
import os
import requests

BASE_DIR   = os.path.dirname(os.path.dirname(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "airlines.json")
CSV_URL    = "https://raw.githubusercontent.com/npow/airline-codes/master/airlines.json"

# Pretty display names that override the database for common regionals
_OVERRIDES = {
    "ENY": "American Eagle",   "JIA": "American Eagle",
    "RPA": "United Express",   "GJS": "United Express",
    "SKW": "SkyWest Airlines", "EDV": "Delta Connection",
    "CPZ": "United Express",   "ASQ": "Delta Connection",
    "TIV": "Thrive Aviation",
}

_db     = {}
_loaded = False


def _download_and_build():
    print("[Airlines] Downloading airline database...")
    try:
        r = requests.get(CSV_URL, timeout=30)
        r.raise_for_status()
        airlines = r.json()
        db = {}
        for a in airlines:
            name = a.get("name", "").strip()
            icao = a.get("icao", "").strip().upper()
            iata = a.get("iata", "").strip().upper()
            if not name or name == "Private flight":
                continue
            if icao and icao != "N/A" and len(icao) == 3:
                db[icao] = _OVERRIDES.get(icao, name)
            if iata and iata != "-" and len(iata) == 2:
                db[iata] = _OVERRIDES.get(icao, name)
        # Apply overrides
        db.update(_OVERRIDES)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f)
        print(f"[Airlines] Database built — {len(db)} entries cached")
        return db
    except Exception as e:
        print(f"[Airlines] Download failed: {e} — using built-in list")
        return dict(_OVERRIDES)


def _load():
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


def get_airline_name(icao):
    """Look up airline display name by ICAO code. Returns empty string if not found."""
    if not icao:
        return ""
    _load()
    return _db.get(icao.upper(), "")


def refresh():
    """Force re-download of airline database."""
    global _db, _loaded
    _loaded = False
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    _load()


if __name__ == "__main__":
    refresh()
    for code in ["UAL","AAL","DAL","SKW","GJS","RPA","ENY","QTR","ETD","UAE","DLH","KAL"]:
        print(f"{code}: {get_airline_name(code)}")
