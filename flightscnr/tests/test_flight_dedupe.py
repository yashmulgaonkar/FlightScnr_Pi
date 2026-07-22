"""Tests for FR24/ADS-B flight deduplication."""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestFlightDedupe(unittest.TestCase):
    def test_identity_keys_match_registration_to_adsb_callsign(self):
        from utilities.aircraft_alert import flight_identity_keys, flights_share_identity

        fr24 = {
            "callsign": "",
            "registration": "N445DB",
            "icao_hex": "A55DB1",
            "plane": "GL5T",
        }
        adsb = {
            "callsign": "N445DB",
            "icao_hex": "A55DB1",
            "plane": "GL5T",
        }
        self.assertTrue(flights_share_identity(fr24, adsb))
        self.assertTrue(any(k.startswith("reg:") for k in flight_identity_keys(fr24)))

    def test_dedupe_merges_dual_feed_pair(self):
        from utilities import aircraft_alert

        fr24 = {
            "callsign": "",
            "registration": "N445DB",
            "plane": "GL5T",
            "plane_latitude": 37.80,
            "plane_longitude": -122.30,
            "altitude": 3425,
            "data_source": "fr24_grpc",
            "origin": "SFO",
            "destination": "LAS",
        }
        adsb = {
            "callsign": "N445DB",
            "plane": "GL5T",
            "plane_latitude": 37.805,
            "plane_longitude": -122.295,
            "altitude": 3200,
            "data_source": "adsb_fi",
            "icao_hex": "A55DB1",
        }
        with mock.patch.object(aircraft_alert.geo, "distance_km", return_value=0.7):
            out = aircraft_alert.dedupe_flights([fr24, adsb])
        self.assertEqual(len(out), 1)
        merged = out[0]
        self.assertEqual(merged.get("callsign"), "N445DB")
        self.assertIn(merged.get("icao_hex"), ("A55DB1", "a55db1", "A55DB1"))

    def test_dedupe_keeps_distant_same_type(self):
        from utilities import aircraft_alert

        a = {
            "callsign": "N111AA",
            "plane": "GL5T",
            "plane_latitude": 37.80,
            "plane_longitude": -122.30,
            "altitude": 3000,
            "data_source": "adsb_fi",
        }
        b = {
            "callsign": "N222BB",
            "plane": "GL5T",
            "plane_latitude": 37.90,
            "plane_longitude": -122.40,
            "altitude": 3100,
            "data_source": "adsb_fi",
        }
        with mock.patch.object(aircraft_alert.geo, "distance_km", return_value=12.0):
            out = aircraft_alert.dedupe_flights([a, b])
        self.assertEqual(len(out), 2)

    def test_merge_preserves_existing_aircraft_type(self):
        """ADS-B must not clobber a known FR24 type (N3XS RV8 vs WAIX)."""
        from utilities.aircraft_alert import merge_live_fields

        target = {"plane": "RV8", "altitude": 3500}
        source = {"plane": "WAIX", "altitude": 3600, "heading": 90}
        merge_live_fields(
            target,
            source,
            ("altitude", "heading", "plane"),
        )
        self.assertEqual(target["plane"], "RV8")
        self.assertEqual(target["altitude"], 3600)
        self.assertEqual(target["heading"], 90)

    def test_merge_fills_blank_aircraft_type(self):
        from utilities.aircraft_alert import merge_live_fields

        target = {"plane": "", "altitude": 3500}
        source = {"plane": "RV8", "altitude": 3600}
        merge_live_fields(target, source, ("altitude", "plane"))
        self.assertEqual(target["plane"], "RV8")

    def test_dedupe_preserves_fr24_type_over_adsb(self):
        from utilities import aircraft_alert

        fr24 = {
            "callsign": "N3XS",
            "registration": "N3XS",
            "plane": "RV8",
            "plane_latitude": 37.80,
            "plane_longitude": -122.30,
            "altitude": 3500,
            "data_source": "fr24_grpc",
            "origin": "SQL",
        }
        adsb = {
            "callsign": "N3XS",
            "plane": "WAIX",
            "plane_latitude": 37.801,
            "plane_longitude": -122.301,
            "altitude": 3480,
            "data_source": "adsb_fi",
            "icao_hex": "A00001",
        }
        with mock.patch.object(aircraft_alert.geo, "distance_km", return_value=0.2):
            out = aircraft_alert.dedupe_flights([fr24, adsb])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["plane"], "RV8")


if __name__ == "__main__":
    unittest.main()
