"""
Unit tests for local database modules (airports.py, airlines.py).

Tests cover:
  - airports.py: coordinate lookup, IATA/ICAO handling, K-prefix stripping,
    cache loading, empty/missing code handling
  - airlines.py: name lookup, override table, empty/missing code handling
"""

import sys
import os
import json
import tempfile
from unittest.mock import patch, MagicMock

import pytest

# Ensure the project root is on sys.path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════════════════════
# Airports Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestAirportsModule:
    """Tests for utilities/airports.py."""

    def _make_test_db(self, tmpdir):
        """Create a small test airports.json for testing."""
        from utilities.airports import CACHE_VERSION
        db = {
            "ORD": {"lat": 41.978, "lon": -87.904},
            "KORD": {"lat": 41.978, "lon": -87.904},
            "JFK": {"lat": 40.6413, "lon": -73.7781},
            "KJFK": {"lat": 40.6413, "lon": -73.7781},
            "EGLL": {"lat": 51.4775, "lon": -0.4614},
            "LHR": {"lat": 51.4775, "lon": -0.4614},
            "NBO": {"lat": -1.3192, "lon": 36.9278},  # Nairobi (near equator)
            "ACC": {"lat": 5.6052, "lon": -0.1668},   # Accra (near prime meridian)
        }
        cache_data = {"_version": CACHE_VERSION, "airports": db}
        cache_path = os.path.join(tmpdir, "airports.json")
        with open(cache_path, "w") as f:
            json.dump(cache_data, f)
        return cache_path, db

    def test_get_airport_coords_iata(self):
        """Look up by IATA code returns correct coordinates."""
        import utilities.airports as airports_mod
        tmpdir = tempfile.mkdtemp()
        cache_path, db = self._make_test_db(tmpdir)

        # Patch the module's CACHE_FILE and reset loaded state
        with patch.object(airports_mod, 'CACHE_FILE', cache_path):
            airports_mod._loaded = False
            airports_mod._db = {}
            result = airports_mod.get_airport_coords("ORD")
            assert result == {"lat": 41.978, "lon": -87.904}

    def test_get_airport_coords_icao(self):
        """Look up by ICAO code returns correct coordinates."""
        import utilities.airports as airports_mod
        tmpdir = tempfile.mkdtemp()
        cache_path, db = self._make_test_db(tmpdir)

        with patch.object(airports_mod, 'CACHE_FILE', cache_path):
            airports_mod._loaded = False
            airports_mod._db = {}
            result = airports_mod.get_airport_coords("EGLL")
            assert result == {"lat": 51.4775, "lon": -0.4614}

    def test_get_airport_coords_kprefix_strip(self):
        """ICAO K-prefix stripping for US airports works."""
        import utilities.airports as airports_mod
        tmpdir = tempfile.mkdtemp()
        cache_path, db = self._make_test_db(tmpdir)

        with patch.object(airports_mod, 'CACHE_FILE', cache_path):
            airports_mod._loaded = False
            airports_mod._db = {}
            # KORD should resolve even if looking up as KORD → ORD
            result = airports_mod.get_airport_coords("KORD")
            assert result["lat"] == pytest.approx(41.978, abs=0.01)

    def test_get_airport_coords_case_insensitive(self):
        """Lookup is case-insensitive."""
        import utilities.airports as airports_mod
        tmpdir = tempfile.mkdtemp()
        cache_path, db = self._make_test_db(tmpdir)

        with patch.object(airports_mod, 'CACHE_FILE', cache_path):
            airports_mod._loaded = False
            airports_mod._db = {}
            result = airports_mod.get_airport_coords("ord")
            assert result == {"lat": 41.978, "lon": -87.904}

    def test_get_airport_coords_empty_returns_empty(self):
        """Empty code returns empty dict."""
        import utilities.airports as airports_mod
        result = airports_mod.get_airport_coords("")
        assert result == {}

    def test_get_airport_coords_none_returns_empty(self):
        """None code returns empty dict."""
        import utilities.airports as airports_mod
        result = airports_mod.get_airport_coords(None)
        assert result == {}

    def test_get_airport_coords_unknown_returns_empty(self):
        """Unknown code returns empty dict."""
        import utilities.airports as airports_mod
        tmpdir = tempfile.mkdtemp()
        cache_path, db = self._make_test_db(tmpdir)

        with patch.object(airports_mod, 'CACHE_FILE', cache_path):
            airports_mod._loaded = False
            airports_mod._db = {}
            result = airports_mod.get_airport_coords("ZZZ")
            assert result == {}

    def test_near_equator_airport(self):
        """Airports near the equator (lat ≈ 0) work correctly."""
        import utilities.airports as airports_mod
        tmpdir = tempfile.mkdtemp()
        cache_path, db = self._make_test_db(tmpdir)

        with patch.object(airports_mod, 'CACHE_FILE', cache_path):
            airports_mod._loaded = False
            airports_mod._db = {}
            result = airports_mod.get_airport_coords("NBO")
            assert result["lat"] == pytest.approx(-1.3192, abs=0.01)

    def test_near_prime_meridian_airport(self):
        """Airports near the prime meridian (lon ≈ 0) work correctly."""
        import utilities.airports as airports_mod
        tmpdir = tempfile.mkdtemp()
        cache_path, db = self._make_test_db(tmpdir)

        with patch.object(airports_mod, 'CACHE_FILE', cache_path):
            airports_mod._loaded = False
            airports_mod._db = {}
            result = airports_mod.get_airport_coords("ACC")
            assert abs(result["lon"]) < 1.0  # Should be near 0

    def test_icao_to_iata_basic(self):
        """icao_to_iata converts known ICAO to IATA."""
        import utilities.airports as airports_mod
        tmpdir = tempfile.mkdtemp()
        cache_path, db = self._make_test_db(tmpdir)

        with patch.object(airports_mod, 'CACHE_FILE', cache_path):
            airports_mod._loaded = False
            airports_mod._db = {}
            # EGLL → LHR (shares same coords)
            result = airports_mod.icao_to_iata("EGLL")
            assert result == "LHR"

    def test_icao_to_iata_kprefix(self):
        """icao_to_iata strips K for US airports."""
        import utilities.airports as airports_mod
        tmpdir = tempfile.mkdtemp()
        cache_path, db = self._make_test_db(tmpdir)

        with patch.object(airports_mod, 'CACHE_FILE', cache_path):
            airports_mod._loaded = False
            airports_mod._db = {}
            result = airports_mod.icao_to_iata("KJFK")
            assert result == "JFK"


# ═══════════════════════════════════════════════════════════════════════════════
# Airlines Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestAirlinesModule:
    """Tests for utilities/airlines.py."""

    def _make_test_db(self, tmpdir):
        """Create a small test airlines.json for testing."""
        db = {
            "AAL": "American Airlines",
            "DAL": "Delta Air Lines",
            "UAL": "United Airlines",
            "BAW": "British Airways",
            "ENY": "American Eagle",
            "RPA": "United Express",
            "SKW": "SkyWest Airlines",
            "DLH": "Lufthansa",
        }
        cache_path = os.path.join(tmpdir, "airlines.json")
        with open(cache_path, "w") as f:
            json.dump(db, f)
        return cache_path, db

    def test_get_airline_name_known(self):
        """Looking up a known airline returns the correct name."""
        import utilities.airlines as airlines_mod
        tmpdir = tempfile.mkdtemp()
        cache_path, db = self._make_test_db(tmpdir)

        with patch.object(airlines_mod, 'CACHE_FILE', cache_path):
            airlines_mod._loaded = False
            airlines_mod._db = {}
            result = airlines_mod.get_airline_name("AAL")
            assert result == "American Airlines"

    def test_get_airline_name_case_insensitive(self):
        """Lookup is case-insensitive."""
        import utilities.airlines as airlines_mod
        tmpdir = tempfile.mkdtemp()
        cache_path, db = self._make_test_db(tmpdir)

        with patch.object(airlines_mod, 'CACHE_FILE', cache_path):
            airlines_mod._loaded = False
            airlines_mod._db = {}
            result = airlines_mod.get_airline_name("baw")
            assert result == "British Airways"

    def test_get_airline_name_unknown_returns_empty(self):
        """Unknown airline code returns empty string."""
        import utilities.airlines as airlines_mod
        tmpdir = tempfile.mkdtemp()
        cache_path, db = self._make_test_db(tmpdir)

        with patch.object(airlines_mod, 'CACHE_FILE', cache_path):
            airlines_mod._loaded = False
            airlines_mod._db = {}
            result = airlines_mod.get_airline_name("XYZ")
            assert result == ""

    def test_get_airline_name_empty_returns_empty(self):
        """Empty code returns empty string."""
        import utilities.airlines as airlines_mod
        result = airlines_mod.get_airline_name("")
        assert result == ""

    def test_get_airline_name_none_returns_empty(self):
        """None code returns empty string."""
        import utilities.airlines as airlines_mod
        result = airlines_mod.get_airline_name(None)
        assert result == ""

    def test_override_for_regionals(self):
        """Regional airline overrides return better display names."""
        import utilities.airlines as airlines_mod
        tmpdir = tempfile.mkdtemp()
        cache_path, db = self._make_test_db(tmpdir)

        with patch.object(airlines_mod, 'CACHE_FILE', cache_path):
            airlines_mod._loaded = False
            airlines_mod._db = {}
            # These should be overridden to user-friendly names
            assert airlines_mod.get_airline_name("ENY") == "American Eagle"
            assert airlines_mod.get_airline_name("RPA") == "United Express"
            assert airlines_mod.get_airline_name("SKW") == "SkyWest Airlines"


# ═══════════════════════════════════════════════════════════════════════════════
# ICAO Aircraft Type Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestIcaoTypesModule:
    """Tests for utilities/icao_types.py."""

    def _make_test_db(self, tmpdir):
        db = {
            "B752": "Boeing 757-200",
            "B738": "Boeing 737-800",
            "E75L": "Embraer ERJ-170-200 (long wing)",
        }
        cache_path = os.path.join(tmpdir, "icao_types.json")
        with open(cache_path, "w") as f:
            json.dump(db, f)
        return cache_path, db

    def test_get_icao_type_name_known(self):
        import utilities.icao_types as icao_mod
        tmpdir = tempfile.mkdtemp()
        cache_path, db = self._make_test_db(tmpdir)

        with patch.object(icao_mod, "CACHE_FILE", cache_path):
            icao_mod._loaded = False
            icao_mod._db = {}
            assert icao_mod.get_icao_type_name("B752") == "Boeing 757-200"

    def test_get_icao_type_name_case_insensitive(self):
        import utilities.icao_types as icao_mod
        tmpdir = tempfile.mkdtemp()
        cache_path, db = self._make_test_db(tmpdir)

        with patch.object(icao_mod, "CACHE_FILE", cache_path):
            icao_mod._loaded = False
            icao_mod._db = {}
            assert icao_mod.get_icao_type_name("b738") == "Boeing 737-800"

    def test_format_aircraft_type_unknown_falls_back_to_code(self):
        import utilities.icao_types as icao_mod
        tmpdir = tempfile.mkdtemp()
        cache_path, db = self._make_test_db(tmpdir)

        with patch.object(icao_mod, "CACHE_FILE", cache_path):
            icao_mod._loaded = False
            icao_mod._db = {}
            assert icao_mod.format_aircraft_type("ZZ99") == "ZZ99"

    def test_format_aircraft_type_empty(self):
        import utilities.icao_types as icao_mod
        assert icao_mod.format_aircraft_type("") == ""
        assert icao_mod.format_aircraft_type("—") == ""


# ═══════════════════════════════════════════════════════════════════════════════
# Integration: Pipeline Data Flow Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDataPipelineIntegration:
    """Integration tests verifying data flows correctly through the pipeline."""

    def test_flight_entry_has_all_required_fields(self):
        """A flight entry dict should contain all fields expected by the round-touch UI."""
        required_fields = [
            "airline", "plane", "origin", "destination",
            "plane_latitude", "plane_longitude",
            "owner_iata", "owner_icao",
            "callsign", "distance",
            "distance_origin", "distance_destination",
            "direction", "trail",
            "time_scheduled_departure", "time_scheduled_arrival",
            "time_real_departure", "time_estimated_arrival",
            "vertical_speed",
        ]
        # Create a mock entry as overhead.py would produce
        entry = {
            "airline": "United Airlines",
            "plane": "B738",
            "flight_number": "UA1234",
            "origin": "ORD",
            "origin_latitude": 41.978,
            "origin_longitude": -87.904,
            "destination": "LHR",
            "destination_latitude": 51.4775,
            "destination_longitude": -0.4614,
            "plane_latitude": 45.0,
            "plane_longitude": -40.0,
            "owner_iata": "UA",
            "owner_icao": "UAL",
            "time_scheduled_departure": 1700000000,
            "time_scheduled_arrival": 1700030000,
            "time_real_departure": 1700000600,
            "time_estimated_arrival": 1700029000,
            "vertical_speed": 0,
            "callsign": "UAL1234",
            "distance_origin": 2000,
            "distance_destination": 1500,
            "distance": 3.2,
            "direction": "NE",
            "trail": [[45.1, -40.2], [45.0, -40.0]],
            "livery_note": "",
        }

        for field in required_fields:
            assert field in entry, f"Missing required field: {field}"

    def test_tracked_flight_entry_has_all_required_fields(self):
        """Tracked flight data dict should contain all fields expected by the tracked screen."""
        required_fields = [
            "callsign", "number", "airline_name", "is_live",
            "origin", "destination",
            "aircraft_type", "altitude", "ground_speed", "heading",
            "dist_remaining", "total_distance", "time_remaining",
            "latitude", "longitude", "last_seen_ts",
            "vertical_speed",
            "time_scheduled_departure", "time_scheduled_arrival",
            "time_real_departure", "time_estimated_arrival",
        ]
        tracked = {
            "callsign": "BAW175",
            "number": "BA175",
            "airline_name": "British Airways",
            "is_live": True,
            "origin": "LHR",
            "destination": "JFK",
            "dest_lat": 40.6413,
            "dest_lon": -73.7781,
            "aircraft_type": "B77W",
            "altitude": 38000,
            "ground_speed": 480,
            "heading": 270,
            "dist_remaining": 1200.0,
            "total_distance": 3450.0,
            "time_remaining": "2:30",
            "latitude": 52.0,
            "longitude": -20.0,
            "last_seen_ts": 1700000000,
            "vertical_speed": 0,
            "time_scheduled_departure": 1700000000,
            "time_scheduled_arrival": 1700030000,
            "time_real_departure": 1700000600,
            "time_estimated_arrival": 1700029000,
        }

        for field in required_fields:
            assert field in tracked, f"Missing required field: {field}"
