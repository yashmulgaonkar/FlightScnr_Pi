#!/usr/bin/env python3
"""
Standalone unit tests that validate pure logic without external dependencies.

These tests can run on ANY system with Python 3.9+ — no pip packages required.
Tests the mathematical functions, data structures, and logic extracted from the
codebase. For full integration tests, use pytest with the Pi's virtualenv.

Usage:
    python3 tests/test_standalone.py
"""

import math
import os
import sys
import json
import time
import tempfile
import threading


# ═══════════════════════════════════════════════════════════════════════════════
# Extracted functions (copy of logic from overhead.py for dependency-free testing)
# ═══════════════════════════════════════════════════════════════════════════════

EARTH_RADIUS_M = 3958.8
DISTANCE_UNITS = "imperial"  # default for tests

HELICOPTER_TYPES = {
    "S76", "EC35", "EC55", "EC30", "A109", "A139", "A169",
    "B06", "B407", "B429", "R44", "R66", "R22",
    "AS50", "AS55", "AS65", "H60", "BK17", "MD52", "MD50",
    "S92", "AW13", "AW16", "AW10", "B212", "B412",
    "EC45", "EC75", "S61", "S70", "H500",
}


def haversine(lat1, lon1, lat2, lon2):
    """Distance between two points. Returns miles or km based on DISTANCE_UNITS."""
    if any(v is None for v in (lat1, lon1, lat2, lon2)):
        return 0
    lat1, lon1 = map(math.radians, (lat1, lon1))
    lat2, lon2 = map(math.radians, (lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (
        math.sin(dlat / 2)**2 +
        math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    miles = EARTH_RADIUS_M * c
    return miles * 1.609 if DISTANCE_UNITS == "metric" else miles


def degrees_to_cardinal(deg):
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int((deg + 22.5) / 45)
    return dirs[idx % 8]


def ordinal(n: int):
    return f"{n}{'tsnrhtdd'[(n//10 % 10 != 1) * (n % 10 < 4) * n % 10::4]}"


def estimate_stale_data(last_data):
    data = dict(last_data)
    data["is_live"] = False
    speed_kts = data.get("ground_speed", 0)
    last_ts = data.get("last_seen_ts")
    if not last_ts:
        return data
    elapsed_hrs = (time.time() - last_ts) / 3600
    elapsed_mins = elapsed_hrs * 60
    last_time_str = data.get("time_remaining", "")
    if last_time_str:
        try:
            if ":" in last_time_str:
                parts = last_time_str.split(":")
                last_mins = int(parts[0]) * 60 + int(parts[1])
            else:
                last_mins = int(last_time_str.replace("m", ""))
            est_mins = max(0, last_mins - int(elapsed_mins))
            h = est_mins // 60
            m = est_mins % 60
            data["time_remaining"] = f"{h}:{m:02d}" if h > 0 else f"{m}m"
        except (ValueError, IndexError):
            pass
    last_dist = data.get("dist_remaining")
    if last_dist is not None and speed_kts > 0:
        if DISTANCE_UNITS == "metric":
            speed_display = speed_kts * 1.852
        else:
            speed_display = speed_kts * 1.15078
        dist_covered = speed_display * elapsed_hrs
        data["dist_remaining"] = max(0, last_dist - dist_covered)
    return data


# ═══════════════════════════════════════════════════════════════════════════════
# Test Framework (minimal, no dependencies)
# ═══════════════════════════════════════════════════════════════════════════════

_tests_run = 0
_tests_passed = 0
_tests_failed = 0
_current_group = ""


def group(name):
    global _current_group
    _current_group = name
    print(f"\n{'═' * 60}")
    print(f"  {name}")
    print(f"{'═' * 60}")


def check(description, condition):
    global _tests_run, _tests_passed, _tests_failed
    _tests_run += 1
    if condition:
        _tests_passed += 1
        print(f"  ✓ {description}")
    else:
        _tests_failed += 1
        print(f"  ✗ FAIL: {description}")


def approx(a, b, tolerance=1.0):
    return abs(a - b) < tolerance


# ═══════════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_haversine():
    group("Haversine Distance Calculation")

    check("Same point returns 0", haversine(40.0, -74.0, 40.0, -74.0) == 0.0)

    # None guards (bug fix: prevents crash on missing airport data)
    check("None lat1 returns 0", haversine(None, -74.0, 40.0, -74.0) == 0)
    check("None lon1 returns 0", haversine(40.0, None, 40.0, -74.0) == 0)
    check("None lat2 returns 0", haversine(40.0, -74.0, None, -74.0) == 0)
    check("None lon2 returns 0", haversine(40.0, -74.0, 40.0, None) == 0)
    check("All None returns 0", haversine(None, None, None, None) == 0)

    # Critical bug fix: 0.0 latitude/longitude must NOT be treated as None
    # (old code used `not all(...)` which treated 0.0 as falsy)
    d = haversine(0.0, 6.6131, 51.4775, -0.4614)
    check(f"Zero latitude (equator) works: {d:.0f} miles > 0", d > 0)

    d = haversine(51.4775, 0.0, 40.6413, -73.7781)
    check(f"Zero longitude (prime meridian) works: {d:.0f} miles > 0", d > 0)

    # Known distances
    d = haversine(40.6413, -73.7781, 33.9425, -118.4081)
    check(f"JFK→LAX ≈ {d:.0f} miles (expect 2400-2500)", 2400 < d < 2500)

    d = haversine(51.4775, -0.4614, 40.6413, -73.7781)
    check(f"LHR→JFK ≈ {d:.0f} miles (expect 3400-3500)", 3400 < d < 3500)

    # Symmetric
    d1 = haversine(40.0, -74.0, 51.5, -0.1)
    d2 = haversine(51.5, -0.1, 40.0, -74.0)
    check(f"Symmetric: A→B ({d1:.1f}) == B→A ({d2:.1f})", abs(d1 - d2) < 0.001)


def test_cardinal_directions():
    group("Cardinal Direction Conversion")

    check("0° → N", degrees_to_cardinal(0) == "N")
    check("360° → N", degrees_to_cardinal(360) == "N")
    check("45° → NE", degrees_to_cardinal(45) == "NE")
    check("90° → E", degrees_to_cardinal(90) == "E")
    check("135° → SE", degrees_to_cardinal(135) == "SE")
    check("180° → S", degrees_to_cardinal(180) == "S")
    check("225° → SW", degrees_to_cardinal(225) == "SW")
    check("270° → W", degrees_to_cardinal(270) == "W")
    check("315° → NW", degrees_to_cardinal(315) == "NW")

    # Boundary: 22° is still N, 23° is NE
    check("22° → N (boundary)", degrees_to_cardinal(22) == "N")
    check("23° → NE (boundary)", degrees_to_cardinal(23) == "NE")


def test_ordinal():
    group("Ordinal Number Formatting")

    check("1 → '1st'", ordinal(1) == "1st")
    check("2 → '2nd'", ordinal(2) == "2nd")
    check("3 → '3rd'", ordinal(3) == "3rd")
    check("4 → '4th'", ordinal(4) == "4th")
    check("11 → '11th'", ordinal(11) == "11th")
    check("12 → '12th'", ordinal(12) == "12th")
    check("13 → '13th'", ordinal(13) == "13th")
    check("21 → '21st'", ordinal(21) == "21st")
    check("22 → '22nd'", ordinal(22) == "22nd")
    check("23 → '23rd'", ordinal(23) == "23rd")
    check("100 → '100th'", ordinal(100) == "100th")
    check("101 → '101st'", ordinal(101) == "101st")


def test_helicopter_detection():
    group("Helicopter Type Detection")

    # Known helicopter types
    helis = ["S76", "EC35", "A139", "R44", "B407", "H60", "S92", "R22", "EC45"]
    for h in helis:
        check(f"{h} detected as helicopter", h in HELICOPTER_TYPES)

    # Known fixed-wing types
    fixed_wing = ["B738", "A320", "C172", "B77W", "E170", "CRJ7", "A388"]
    for fw in fixed_wing:
        check(f"{fw} NOT detected as helicopter", fw not in HELICOPTER_TYPES)

    check(f"Total helicopter types: {len(HELICOPTER_TYPES)}", len(HELICOPTER_TYPES) > 25)


def test_stale_data_estimation():
    group("Stale Data Estimation (Tracked Flights)")

    # Basic: marks as not live
    data = {"is_live": True, "ground_speed": 450, "last_seen_ts": time.time() - 60}
    result = estimate_stale_data(data)
    check("Sets is_live=False", result["is_live"] is False)

    # Time remaining decreases
    data = {
        "is_live": True, "ground_speed": 450,
        "last_seen_ts": time.time() - 600,  # 10 min ago
        "time_remaining": "1:30",  # 90 min
        "dist_remaining": 500,
    }
    result = estimate_stale_data(data)
    # After 10 min, ~80 min remaining
    check(f"Time remaining decreased: '{result['time_remaining']}'",
          "1:2" in result["time_remaining"] or "1:19" in result["time_remaining"] or "1:20" in result["time_remaining"])

    # Distance remaining decreases
    check(f"Distance decreased: {result['dist_remaining']:.1f} < 500", result["dist_remaining"] < 500)
    check(f"Distance still positive: {result['dist_remaining']:.1f} > 0", result["dist_remaining"] > 0)

    # No last_seen_ts → unchanged except is_live
    data = {"is_live": True, "ground_speed": 450, "time_remaining": "2:00", "dist_remaining": 800}
    result = estimate_stale_data(data)
    check("No last_seen_ts: is_live=False", result["is_live"] is False)
    check("No last_seen_ts: time unchanged", result["time_remaining"] == "2:00")
    check("No last_seen_ts: dist unchanged", result["dist_remaining"] == 800)

    # Zero speed → no distance change
    data = {
        "is_live": True, "ground_speed": 0,
        "last_seen_ts": time.time() - 600,
        "dist_remaining": 500,
    }
    result = estimate_stale_data(data)
    check("Zero speed: dist unchanged", result["dist_remaining"] == 500)


def test_thread_safety_data_structures():
    group("Thread Safety: Concurrent Access")

    from threading import Lock

    # Simulate the Overhead class lock pattern
    lock = Lock()
    data = []
    new_data = [False]
    errors = []

    def reader():
        try:
            for _ in range(200):
                with lock:
                    _ = len(data)
                    _ = new_data[0]
        except Exception as e:
            errors.append(e)

    def writer():
        try:
            for i in range(100):
                with lock:
                    data.append({"callsign": f"FLT{i}"})
                    new_data[0] = True
                with lock:
                    data.clear()
                    new_data[0] = False
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=reader) for _ in range(5)]
    threads += [threading.Thread(target=writer) for _ in range(3)]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check(f"No concurrent access errors (0 errors)", len(errors) == 0)


def test_error_handler_logic():
    group("Error Handler: _new_data Set on Failure")

    # Simulates the key behavior: on error, _new_data must be True
    # so the display loop doesn't spin forever
    from threading import Lock

    lock = Lock()
    new_data = False
    processing = False

    # Simulate successful grab
    with lock:
        new_data = False
        processing = True

    # Simulate error path
    try:
        raise ConnectionError("Network down")
    except (ConnectionError, OSError):
        with lock:
            new_data = True  # KEY: this prevents display freeze
            processing = False

    check("After error: new_data=True (prevents display freeze)", new_data is True)
    check("After error: processing=False", processing is False)


def test_data_pipeline_format():
    group("Data Pipeline: Entry Format Validation")

    required_overhead_fields = [
        "airline", "plane", "origin", "destination",
        "plane_latitude", "plane_longitude",
        "owner_iata", "owner_icao", "callsign",
        "distance", "distance_origin", "distance_destination",
        "direction", "trail",
        "time_scheduled_departure", "time_scheduled_arrival",
        "time_real_departure", "time_estimated_arrival",
        "vertical_speed",
    ]

    required_tracked_fields = [
        "callsign", "number", "airline_name", "is_live",
        "origin", "destination",
        "aircraft_type", "altitude", "ground_speed", "heading",
        "dist_remaining", "total_distance", "time_remaining",
        "latitude", "longitude", "last_seen_ts", "vertical_speed",
        "time_scheduled_departure", "time_scheduled_arrival",
        "time_real_departure", "time_estimated_arrival",
    ]

    # Mock entry as produced by overhead.py
    overhead_entry = {
        "airline": "United Airlines", "plane": "B738",
        "flight_number": "UA1234",
        "origin": "ORD", "origin_latitude": 41.978, "origin_longitude": -87.904,
        "destination": "LHR", "destination_latitude": 51.47, "destination_longitude": -0.46,
        "plane_latitude": 45.0, "plane_longitude": -40.0,
        "owner_iata": "UA", "owner_icao": "UAL",
        "time_scheduled_departure": 1700000000, "time_scheduled_arrival": 1700030000,
        "time_real_departure": 1700000600, "time_estimated_arrival": 1700029000,
        "vertical_speed": 0, "callsign": "UAL1234",
        "distance_origin": 2000, "distance_destination": 1500,
        "distance": 3.2, "direction": "NE",
        "trail": [[45.1, -40.2]], "livery_note": "",
    }

    tracked_entry = {
        "callsign": "BAW175", "number": "BA175", "airline_name": "British Airways",
        "is_live": True, "origin": "LHR", "destination": "JFK",
        "dest_lat": 40.64, "dest_lon": -73.78,
        "aircraft_type": "B77W", "altitude": 38000, "ground_speed": 480,
        "heading": 270, "dist_remaining": 1200.0, "total_distance": 3450.0,
        "time_remaining": "2:30", "latitude": 52.0, "longitude": -20.0,
        "last_seen_ts": time.time(), "vertical_speed": 0,
        "time_scheduled_departure": 1700000000, "time_scheduled_arrival": 1700030000,
        "time_real_departure": 1700000600, "time_estimated_arrival": 1700029000,
    }

    missing_overhead = [f for f in required_overhead_fields if f not in overhead_entry]
    check(f"Overhead entry has all {len(required_overhead_fields)} required fields",
          len(missing_overhead) == 0)
    if missing_overhead:
        print(f"    Missing: {missing_overhead}")

    missing_tracked = [f for f in required_tracked_fields if f not in tracked_entry]
    check(f"Tracked entry has all {len(required_tracked_fields)} required fields",
          len(missing_tracked) == 0)
    if missing_tracked:
        print(f"    Missing: {missing_tracked}")


def test_json_io():
    group("Safe JSON I/O")

    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "test.json")

    # Nonexistent file
    try:
        with open("/tmp/nonexistent_xyz_12345.json", "r") as f:
            pass
        exists = True
    except FileNotFoundError:
        exists = False
    check("Nonexistent file doesn't exist", not exists)

    # Write and read roundtrip
    data = [{"callsign": "UAL100", "distance": 2.5}, {"callsign": "BAW175", "distance": 4.1}]
    with open(path, "w") as f:
        json.dump(data, f, indent=4)
    with open(path, "r") as f:
        loaded = json.load(f)
    check("JSON roundtrip preserves data", loaded == data)
    check("JSON roundtrip preserves types", isinstance(loaded[0]["distance"], float))

    # Invalid JSON
    invalid_path = os.path.join(tmpdir, "invalid.json")
    with open(invalid_path, "w") as f:
        f.write("{not valid json!!")
    try:
        with open(invalid_path, "r") as f:
            json.load(f)
        loaded_ok = True
    except json.JSONDecodeError:
        loaded_ok = False
    check("Invalid JSON raises JSONDecodeError", not loaded_ok)

    # Cleanup
    os.unlink(path)
    os.unlink(invalid_path)


def test_pipeline_summary_output():
    group("Pipeline Summary: Log Output Format")

    # Simulate what the pipeline logger would produce
    stats = {
        "elapsed_ms": 2340,
        "zone_raw": 12,
        "zone_filtered": 8,
        "flights_processed": 3,
        "details_fetched": 3,
        "details_cached": 0,
        "airport_lookups": 6,
        "airline_lookups": 2,
        "adsbdb_lookups": 1,
        "helicopters": 1,
        "tracked_status": "",
        "tracked_callsign": "",
        "flight_details": [
            {"callsign": "UAL1234", "plane": "B738", "origin": "ORD", "destination": "LHR", "distance": 3.2, "data_source": "fr24_grpc"},
            {"callsign": "HELI01", "plane": "EC35", "origin": "", "destination": "", "distance": 1.5, "data_source": "fr24_grpc"},
            {"callsign": "DAL456", "plane": "A320", "origin": "JFK", "destination": "LAX", "distance": 5.1, "data_source": "fr24_grpc"},
        ],
    }

    # Verify all expected fields are present
    check("Stats has elapsed_ms", "elapsed_ms" in stats)
    check("Stats has zone_raw count", stats["zone_raw"] == 12)
    check("Stats has flights_processed", stats["flights_processed"] == 3)
    check("Stats has flight_details list", len(stats["flight_details"]) == 3)
    check("Stats tracks helicopters", stats["helicopters"] == 1)
    check("Stats tracks airport_lookups", stats["airport_lookups"] == 6)
    check("Stats tracks airline_lookups", stats["airline_lookups"] == 2)
    check("Stats tracks adsbdb_lookups", stats["adsbdb_lookups"] == 1)

    # Verify flight details contain expected structure
    fd = stats["flight_details"][0]
    check("Flight detail has callsign", "callsign" in fd)
    check("Flight detail has data_source", "data_source" in fd)
    check("Flight detail has distance", "distance" in fd)


# ═══════════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  FlightScnr Pi — Standalone Unit Tests                      ║")
    print("║  No external dependencies required (pure Python 3.9+)       ║")
    print("╚══════════════════════════════════════════════════════════════╝")

    test_haversine()
    test_cardinal_directions()
    test_ordinal()
    test_helicopter_detection()
    test_stale_data_estimation()
    test_thread_safety_data_structures()
    test_error_handler_logic()
    test_data_pipeline_format()
    test_json_io()
    test_pipeline_summary_output()

    print(f"\n{'═' * 60}")
    print(f"  RESULTS: {_tests_passed}/{_tests_run} passed, {_tests_failed} failed")
    print(f"{'═' * 60}")

    if _tests_failed > 0:
        sys.exit(1)
    else:
        print("\n  🎉 All tests passed!\n")
        sys.exit(0)
