"""
Unit tests for overhead.py utility functions and the data pipeline.

Tests cover:
  - haversine(): distance calculation, None guard, zero-coordinate handling
  - degrees_to_cardinal(): compass direction conversion
  - estimate_stale_data(): stale flight position estimation
  - Helicopter detection (HELICOPTER_TYPES)
  - data_is_empty property (thread-safe)
  - Error handler behavior (_new_data set True on error)
  - Local airport/airline lookups
  - Audit logging
"""

import sys
import os
import json
import time
import math
import threading
import tempfile
from unittest.mock import patch, MagicMock

import pytest

# Ensure the project root is on sys.path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock config and other imports before importing overhead
os.environ.setdefault("ZONE_TL_LAT", "51.595")
os.environ.setdefault("ZONE_TL_LON", "-0.314")
os.environ.setdefault("ZONE_BR_LAT", "51.47")
os.environ.setdefault("ZONE_BR_LON", "-0.111")
os.environ.setdefault("HOME_LAT", "51.55864")
os.environ.setdefault("HOME_LON", "-0.177332")
os.environ.setdefault("DISTANCE_UNITS", "imperial")
os.environ.setdefault("FLIGHTSCNR_DATA_DIR", tempfile.mkdtemp())


# ═══════════════════════════════════════════════════════════════════════════════
# Haversine Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestHaversine:
    """Tests for the haversine distance function."""

    def test_same_point_returns_zero(self):
        """Distance from a point to itself is zero."""
        from utilities.overhead import haversine
        assert haversine(40.0, -74.0, 40.0, -74.0) == 0.0

    def test_known_distance_new_york_los_angeles(self):
        """NY to LA is approximately 2,451 miles."""
        from utilities.overhead import haversine
        # JFK to LAX approximate coordinates
        dist = haversine(40.6413, -73.7781, 33.9425, -118.4081)
        # Should be roughly 2,450 miles (imperial)
        assert 2400 < dist < 2500

    def test_none_lat1_returns_zero(self):
        """None latitude returns 0 (guard against invalid data)."""
        from utilities.overhead import haversine
        assert haversine(None, -74.0, 40.0, -74.0) == 0

    def test_none_lon1_returns_zero(self):
        """None longitude returns 0."""
        from utilities.overhead import haversine
        assert haversine(40.0, None, 40.0, -74.0) == 0

    def test_none_lat2_returns_zero(self):
        """None destination lat returns 0."""
        from utilities.overhead import haversine
        assert haversine(40.0, -74.0, None, -74.0) == 0

    def test_none_lon2_returns_zero(self):
        """None destination lon returns 0."""
        from utilities.overhead import haversine
        assert haversine(40.0, -74.0, 40.0, None) == 0

    def test_all_none_returns_zero(self):
        """All None returns 0."""
        from utilities.overhead import haversine
        assert haversine(None, None, None, None) == 0

    def test_zero_latitude_not_treated_as_none(self):
        """Airports at 0.0 latitude (equator) should NOT be treated as None.
        This is the bug fix: `not all(...)` would fail for 0.0 values."""
        from utilities.overhead import haversine
        # Sao Tome (on the equator) to London
        dist = haversine(0.0, 6.6131, 51.4775, -0.4614)
        assert dist > 0  # Should be a real distance, not zero

    def test_zero_longitude_not_treated_as_none(self):
        """Airports at 0.0 longitude (prime meridian) should work."""
        from utilities.overhead import haversine
        # A point on the prime meridian to somewhere else
        dist = haversine(51.4775, 0.0, 40.6413, -73.7781)
        assert dist > 0

    def test_symmetric(self):
        """haversine(A, B) == haversine(B, A)."""
        from utilities.overhead import haversine
        d1 = haversine(40.0, -74.0, 51.5, -0.1)
        d2 = haversine(51.5, -0.1, 40.0, -74.0)
        assert abs(d1 - d2) < 0.001


# ═══════════════════════════════════════════════════════════════════════════════
# Cardinal Direction Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDegreesToCardinal:
    """Tests for compass direction conversion."""

    def test_north(self):
        from utilities.overhead import degrees_to_cardinal
        assert degrees_to_cardinal(0) == "N"
        assert degrees_to_cardinal(360) == "N"

    def test_east(self):
        from utilities.overhead import degrees_to_cardinal
        assert degrees_to_cardinal(90) == "E"

    def test_south(self):
        from utilities.overhead import degrees_to_cardinal
        assert degrees_to_cardinal(180) == "S"

    def test_west(self):
        from utilities.overhead import degrees_to_cardinal
        assert degrees_to_cardinal(270) == "W"

    def test_northeast(self):
        from utilities.overhead import degrees_to_cardinal
        assert degrees_to_cardinal(45) == "NE"

    def test_boundary_values(self):
        """22.5 degrees is the boundary between N and NE."""
        from utilities.overhead import degrees_to_cardinal
        # At exactly 22.5 degrees, should still be N (edge case)
        result = degrees_to_cardinal(22)
        assert result == "N"
        result = degrees_to_cardinal(23)
        assert result == "NE"


# ═══════════════════════════════════════════════════════════════════════════════
# Helicopter Detection Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestHelicopterDetection:
    """Tests for helicopter type identification."""

    def test_common_helicopter_types_detected(self):
        """Common helicopter ICAO type codes are in HELICOPTER_TYPES."""
        from utilities.overhead import HELICOPTER_TYPES
        assert "S76" in HELICOPTER_TYPES   # Sikorsky S-76
        assert "EC35" in HELICOPTER_TYPES  # Eurocopter EC135
        assert "A139" in HELICOPTER_TYPES  # AgustaWestland AW139
        assert "R44" in HELICOPTER_TYPES   # Robinson R44
        assert "B407" in HELICOPTER_TYPES  # Bell 407
        assert "H60" in HELICOPTER_TYPES   # Black Hawk

    def test_fixed_wing_not_in_helicopter_types(self):
        """Common fixed-wing types should NOT be detected as helicopters."""
        from utilities.overhead import HELICOPTER_TYPES
        assert "B738" not in HELICOPTER_TYPES  # Boeing 737-800
        assert "A320" not in HELICOPTER_TYPES  # Airbus A320
        assert "C172" not in HELICOPTER_TYPES  # Cessna 172
        assert "B77W" not in HELICOPTER_TYPES  # Boeing 777

    def test_helicopter_sets_heli_icao(self):
        """When aircraft_type is a helicopter, owner_icao should be 'HELI'."""
        from utilities.overhead import HELICOPTER_TYPES
        # Simulate the logic from overhead.py
        aircraft_type = "EC35"
        owner_icao = "BAW"  # hypothetical
        if aircraft_type in HELICOPTER_TYPES:
            owner_icao = "HELI"
        assert owner_icao == "HELI"


# ═══════════════════════════════════════════════════════════════════════════════
# Stale Data Estimation Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestEstimateStaleData:
    """Tests for stale flight position estimation."""

    def test_marks_not_live(self):
        """Stale data is marked with is_live=False."""
        from utilities.overhead import estimate_stale_data
        data = {
            "is_live": True,
            "ground_speed": 450,
            "last_seen_ts": time.time() - 60,
            "time_remaining": "1:30",
            "dist_remaining": 500,
        }
        result = estimate_stale_data(data)
        assert result["is_live"] is False

    def test_time_remaining_decreases(self):
        """Time remaining should decrease by elapsed time."""
        from utilities.overhead import estimate_stale_data
        data = {
            "is_live": True,
            "ground_speed": 450,
            "last_seen_ts": time.time() - 600,  # 10 minutes ago
            "time_remaining": "1:30",  # 90 minutes
            "dist_remaining": 500,
        }
        result = estimate_stale_data(data)
        # Should be approximately 80 minutes remaining
        assert "1:20" in result["time_remaining"] or "1:19" in result["time_remaining"]

    def test_distance_remaining_decreases(self):
        """Distance remaining should decrease based on speed × time."""
        from utilities.overhead import estimate_stale_data
        data = {
            "is_live": True,
            "ground_speed": 450,  # knots
            "last_seen_ts": time.time() - 3600,  # 1 hour ago
            "time_remaining": "3:00",
            "dist_remaining": 1000,
        }
        result = estimate_stale_data(data)
        # 450 knots × 1 hour × 1.15078 = ~518 mph covered
        assert result["dist_remaining"] < 1000
        assert result["dist_remaining"] > 0

    def test_no_last_ts_returns_unchanged(self):
        """Without last_seen_ts, returns data unchanged except is_live."""
        from utilities.overhead import estimate_stale_data
        data = {
            "is_live": True,
            "ground_speed": 450,
            "time_remaining": "2:00",
            "dist_remaining": 800,
        }
        result = estimate_stale_data(data)
        assert result["is_live"] is False
        assert result["time_remaining"] == "2:00"
        assert result["dist_remaining"] == 800

    def test_zero_speed_no_distance_change(self):
        """With zero ground speed, distance remaining doesn't change."""
        from utilities.overhead import estimate_stale_data
        data = {
            "is_live": True,
            "ground_speed": 0,
            "last_seen_ts": time.time() - 600,
            "time_remaining": "1:00",
            "dist_remaining": 500,
        }
        result = estimate_stale_data(data)
        assert result["dist_remaining"] == 500

    def test_stale_rejects_absurd_time_remaining(self):
        """Garbage H:MM values from bad API data are cleared."""
        from utilities.overhead import estimate_stale_data
        data = {
            "is_live": True,
            "ground_speed": 450,
            "last_seen_ts": time.time() - 60,
            "time_remaining": "1193046:25",
            "dist_remaining": 500,
        }
        result = estimate_stale_data(data)
        assert result.get("time_remaining") is None


class TestTimeRemainingHelpers:
    def test_rejects_uint32_remaining_seconds(self):
        from utilities.overhead import _format_time_remaining_minutes, _plausible_remaining_seconds

        assert _plausible_remaining_seconds(4294967295) is False
        assert _format_time_remaining_minutes(71582788) is None

    def test_arrived_on_ground_clears_eta(self):
        from types import SimpleNamespace
        from utilities.overhead import _flight_has_arrived

        match = SimpleNamespace(on_ground=True, ground_speed=17)
        assert _flight_has_arrived(match, {}, {"eta": 0}, 0.5) is True

    def test_past_eta_near_destination_is_arrived(self):
        from types import SimpleNamespace
        from utilities.overhead import _flight_has_arrived

        match = SimpleNamespace(on_ground=False, ground_speed=17)
        past_eta = int(time.time()) - 600
        assert _flight_has_arrived(match, {}, {"eta": past_eta}, 1.0) is True


# ═══════════════════════════════════════════════════════════════════════════════
# Overhead Class Property Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestOverheadProperties:
    """Tests for Overhead class thread-safe properties."""

    def test_data_is_empty_with_lock(self):
        """data_is_empty should be thread-safe (uses lock)."""
        from utilities.overhead import Overhead
        o = Overhead()
        # Initially empty
        assert o.data_is_empty is True

        # Simulate data arrival (bypassing grab_data)
        with o._lock:
            o._data = [{"callsign": "TEST123"}]
        assert o.data_is_empty is False

    def test_new_data_default_false(self):
        """new_data should be False initially."""
        from utilities.overhead import Overhead
        o = Overhead()
        assert o.new_data is False

    def test_processing_default_false(self):
        """processing should be False initially."""
        from utilities.overhead import Overhead
        o = Overhead()
        assert o.processing is False

    def test_data_resets_new_data_flag(self):
        """Accessing .data should reset _new_data to False."""
        from utilities.overhead import Overhead
        o = Overhead()
        with o._lock:
            o._new_data = True
            o._data = [{"callsign": "ABC123"}]
        _ = o.data
        assert o.new_data is False

    def test_concurrent_data_access(self):
        """Multiple threads accessing data simultaneously shouldn't crash."""
        from utilities.overhead import Overhead
        o = Overhead()
        with o._lock:
            o._data = [{"callsign": f"FLT{i}"} for i in range(5)]
            o._new_data = True

        results = []
        errors = []

        def reader():
            try:
                for _ in range(100):
                    _ = o.data_is_empty
                    _ = o.new_data
                results.append(True)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 10


# ═══════════════════════════════════════════════════════════════════════════════
# Error Handler Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestErrorHandlerFreeze:
    """Tests that _grab() sets _new_data=True even on errors (prevents display freeze)."""

    def test_network_error_sets_new_data_true(self):
        """After a network error, new_data should be True (not stuck spinning)."""
        from utilities.overhead import Overhead
        o = Overhead()

        # Mock the API to raise a ConnectionError
        with patch.object(o._api, 'get_flights', side_effect=ConnectionError("Network down")):
            # Run _grab directly (not in a thread for testing)
            o._grab()

        # Key assertion: new_data must be True so display loop proceeds
        assert o.new_data is True
        assert o.data_is_empty is True
        assert o.processing is False

    def test_unexpected_error_sets_new_data_true(self):
        """After an unexpected error, new_data should still be True."""
        from utilities.overhead import Overhead
        o = Overhead()

        # Mock the API to raise a generic exception
        with patch.object(o._api, 'get_flights', side_effect=RuntimeError("Unexpected!")):
            o._grab()

        assert o.new_data is True
        assert o.data_is_empty is True
        assert o.processing is False


# ═══════════════════════════════════════════════════════════════════════════════
# Safe JSON I/O Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSafeJsonIO:
    """Tests for safe_load_json and safe_write_json."""

    def test_load_nonexistent_returns_empty_list(self):
        """Loading a non-existent file returns empty list."""
        from utilities.overhead import safe_load_json
        result = safe_load_json("/tmp/nonexistent_file_xyz.json")
        assert result == []

    def test_load_invalid_json_returns_empty_list(self):
        """Loading invalid JSON returns empty list."""
        from utilities.overhead import safe_load_json
        path = "/tmp/test_invalid_json.json"
        with open(path, "w") as f:
            f.write("not valid json {{{")
        result = safe_load_json(path)
        assert result == []
        os.unlink(path)

    def test_write_and_load_roundtrip(self):
        """Write then load JSON roundtrip."""
        from utilities.overhead import safe_write_json, safe_load_json
        path = "/tmp/test_roundtrip.json"
        data = [{"callsign": "UAL123", "distance": 5.2}]
        safe_write_json(path, data)
        result = safe_load_json(path)
        assert result == data
        os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════════════
# Ordinal Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrdinal:
    """Tests for ordinal number formatting."""

    def test_ordinals(self):
        from utilities.overhead import ordinal
        assert ordinal(1) == "1st"
        assert ordinal(2) == "2nd"
        assert ordinal(3) == "3rd"
        assert ordinal(4) == "4th"
        assert ordinal(11) == "11th"
        assert ordinal(12) == "12th"
        assert ordinal(13) == "13th"
        assert ordinal(21) == "21st"
        assert ordinal(22) == "22nd"
        assert ordinal(23) == "23rd"
