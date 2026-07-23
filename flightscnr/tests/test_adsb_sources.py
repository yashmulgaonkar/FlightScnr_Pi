"""Tests for dump1090 client, FlightAware route fallback, and merge preference."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestDump1090Client(unittest.TestCase):
    def test_parses_aircraft_json_within_radius(self):
        from utilities import dump1090_client

        payload = {
            "aircraft": [
                {
                    "hex": "a55db1",
                    "flight": "N445DB ",
                    "r": "N445DB",
                    "t": "GL5T",
                    "lat": 37.80,
                    "lon": -122.30,
                    "alt_baro": 3500,
                    "gs": 220,
                    "track": 90,
                    "baro_rate": 0,
                    "squawk": "1200",
                    "seen_pos": 1.0,
                },
                {
                    # Too far
                    "hex": "abcdef",
                    "flight": "FAR1",
                    "lat": 40.0,
                    "lon": -120.0,
                    "alt_baro": 10000,
                    "seen_pos": 1.0,
                },
            ]
        }
        dump1090_client._CACHE = {"entries": [], "ts": 0.0, "url": None, "radius_nm": None}
        with mock.patch.object(dump1090_client.requests, "get") as get:
            resp = mock.Mock()
            resp.raise_for_status = mock.Mock()
            resp.json.return_value = payload
            get.return_value = resp
            with mock.patch.dict("os.environ", {"DUMP1090_URL": "http://127.0.0.1:8080/data/aircraft.json"}):
                entries = dump1090_client.fetch_aircraft_entries(
                    37.80, -122.30, 20.0, min_altitude=0
                )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["data_source"], "dump1090")
        self.assertEqual(entries[0]["icao_hex"], "A55DB1")
        self.assertEqual(entries[0]["callsign"].strip(), "N445DB")

    def test_normalizes_base_url(self):
        from utilities.dump1090_client import _aircraft_json_url

        self.assertTrue(
            _aircraft_json_url("http://192.168.1.10:8080").endswith("/data/aircraft.json")
        )
        self.assertEqual(
            _aircraft_json_url("http://host/data/aircraft.json"),
            "http://host/data/aircraft.json",
        )


class TestFlightAwareClient(unittest.TestCase):
    def test_lookup_route_parses_origin_dest(self):
        from utilities import flightaware_client

        flightaware_client._cache.clear()
        with tempfile.TemporaryDirectory() as tmp:
            flightaware_client.DATA_DIR = tmp
            flightaware_client.USAGE_PATH = str(Path(tmp) / "fa_usage.json")
            payload = {
                "flights": [
                    {
                        "ident": "UAL123",
                        "status": "En Route / On Time",
                        "origin": {"code_iata": "SFO"},
                        "destination": {"code_iata": "EWR"},
                        "scheduled_out": "2026-07-22T18:00:00Z",
                        "scheduled_in": "2026-07-22T22:00:00Z",
                    }
                ]
            }
            with mock.patch.object(flightaware_client, "_api_key", return_value="test-key"):
                with mock.patch.object(flightaware_client, "api_enabled", create=True):
                    pass
                with mock.patch(
                    "secrets_store.api_enabled", return_value=True, create=True
                ):
                    with mock.patch.object(flightaware_client.requests, "get") as get:
                        resp = mock.Mock()
                        resp.status_code = 200
                        resp.raise_for_status = mock.Mock()
                        resp.json.return_value = payload
                        get.return_value = resp
                        result = flightaware_client.lookup_route("UAL123")
            self.assertIsNotNone(result)
            self.assertEqual(result["origin"], "SFO")
            self.assertEqual(result["destination"], "EWR")
            self.assertEqual(result["route_source"], "flightaware")
            usage = json.loads(Path(flightaware_client.USAGE_PATH).read_text())
            self.assertEqual(usage["calls"], 1)

    def test_budget_blocks_further_calls(self):
        from utilities import flightaware_client

        flightaware_client._cache.clear()
        with tempfile.TemporaryDirectory() as tmp:
            flightaware_client.DATA_DIR = tmp
            flightaware_client.USAGE_PATH = str(Path(tmp) / "fa_usage.json")
            Path(flightaware_client.USAGE_PATH).write_text(
                json.dumps(
                    {
                        "month": flightaware_client._month_key(),
                        "spend_usd": 4.50,
                        "calls": 200,
                    }
                )
            )
            with mock.patch.object(flightaware_client, "_api_key", return_value="test-key"):
                with mock.patch.object(flightaware_client, "_monthly_limit", return_value=4.50):
                    with mock.patch(
                        "secrets_store.api_enabled", return_value=True, create=True
                    ):
                        with mock.patch.object(flightaware_client.requests, "get") as get:
                            result = flightaware_client.lookup_route("UAL999")
                            get.assert_not_called()
            self.assertIsNone(result)


class TestRouteEnrichmentFallback(unittest.TestCase):
    def test_falls_back_to_flightaware(self):
        from utilities import route_enrichment

        flight = {"callsign": "UAL123", "origin": "", "destination": ""}
        with mock.patch.object(route_enrichment, "_from_airlabs", return_value=None):
            with mock.patch.object(
                route_enrichment,
                "_from_flightaware",
                return_value={
                    "origin": "SFO",
                    "destination": "EWR",
                    "dep_time": "",
                    "arr_time": "",
                    "schedule_status": "En Route",
                    "route_source": "flightaware",
                },
            ):
                enr = route_enrichment.fetch_route_enrichment(flight)
        self.assertEqual(enr["origin"], "SFO")
        self.assertEqual(enr["route_source"], "flightaware")


class TestDump1090Richness(unittest.TestCase):
    def test_dump1090_preferred_over_adsb_fi_when_deduping(self):
        from utilities import aircraft_alert

        cloud = {
            "callsign": "N445DB",
            "icao_hex": "A55DB1",
            "plane": "GL5T",
            "plane_latitude": 37.80,
            "plane_longitude": -122.30,
            "altitude": 3500,
            "data_source": "adsb_fi",
        }
        local = {
            "callsign": "N445DB",
            "icao_hex": "A55DB1",
            "plane": "GL5T",
            "plane_latitude": 37.801,
            "plane_longitude": -122.301,
            "altitude": 3480,
            "data_source": "dump1090",
            "squawk": "1200",
        }
        with mock.patch.object(aircraft_alert.geo, "distance_km", return_value=0.2):
            out = aircraft_alert.dedupe_flights([cloud, local])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["data_source"], "dump1090")


if __name__ == "__main__":
    unittest.main()
