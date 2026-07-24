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

    def test_dedupe_merges_stale_fr24_with_blank_adsb(self):
        """FR24 lag can put the icon several km from live ADS-B (QTR5Q/QR737 case)."""
        from utilities import aircraft_alert

        fr24 = {
            "callsign": "QTR5Q",
            "flight_number": "QR737",
            "plane": "A35K",
            "plane_latitude": 37.55,
            "plane_longitude": -122.25,
            "altitude": 5900,
            "data_source": "fr24_grpc",
            "origin": "DOH",
            "destination": "SFO",
            "airline": "Qatar Airways",
        }
        adsb = {
            "callsign": "",
            "plane": "",
            "plane_latitude": 37.48,
            "plane_longitude": -122.38,
            "altitude": 5900,
            "data_source": "adsb_fi",
            "icao_hex": "06A123",
        }
        with mock.patch.object(aircraft_alert.geo, "distance_km", return_value=12.0):
            out = aircraft_alert.dedupe_flights([fr24, adsb])
        self.assertEqual(len(out), 1)
        merged = out[0]
        self.assertEqual(merged.get("callsign"), "QTR5Q")
        self.assertEqual(merged.get("plane"), "A35K")
        # ADS-B kinematics win on the FR24 metadata shell.
        self.assertEqual(merged.get("plane_latitude"), 37.48)
        self.assertEqual(merged.get("icao_hex"), "06A123")

    def test_dedupe_merges_glf5_reg_fr24_with_blank_adsb(self):
        """Business jet: FR24 shows N-number; ADS-B often blank callsign + lagged FR24 pos."""
        from utilities import aircraft_alert

        fr24 = {
            "callsign": "N284PH",
            "registration": "N284PH",
            "plane": "GLF5",
            "plane_latitude": 26.32,
            "plane_longitude": -80.96,
            "altitude": 650,
            "data_source": "fr24_grpc",
        }
        adsb = {
            "callsign": "",
            "plane": "GLF5",
            "plane_latitude": 26.35,
            "plane_longitude": -80.90,
            "altitude": 1075,
            "data_source": "adsb_fi",
            "icao_hex": "A27ABC",
        }
        with mock.patch.object(aircraft_alert.geo, "distance_km", return_value=8.0):
            out = aircraft_alert.dedupe_flights([fr24, adsb])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].get("registration") or out[0].get("callsign"), "N284PH")
        self.assertEqual(out[0].get("plane"), "GLF5")
        self.assertEqual(out[0].get("icao_hex"), "A27ABC")

    def test_cross_feed_wide_merge_requires_fr24_data_source(self):
        """Without data_source on the FR24 shell, 15 km lag merge must not run."""
        from utilities import aircraft_alert

        fr24 = {
            "callsign": "N284PH",
            "registration": "N284PH",
            "plane": "GLF5",
            "plane_latitude": 26.32,
            "plane_longitude": -80.96,
            "altitude": 650,
            # Missing data_source — historical overhead.py bug.
        }
        adsb = {
            "callsign": "",
            "plane": "GLF5",
            "plane_latitude": 26.35,
            "plane_longitude": -80.90,
            "altitude": 1075,
            "data_source": "adsb_fi",
            "icao_hex": "A27ABC",
        }
        with mock.patch.object(aircraft_alert.geo, "distance_km", return_value=8.0):
            out = aircraft_alert.dedupe_flights([fr24, adsb])
        self.assertEqual(len(out), 2)

    def test_dedupe_keeps_cross_feed_with_conflicting_callsigns(self):
        from utilities import aircraft_alert

        fr24 = {
            "callsign": "QTR5Q",
            "plane": "A35K",
            "plane_latitude": 37.55,
            "plane_longitude": -122.25,
            "altitude": 5900,
            "data_source": "fr24_grpc",
        }
        adsb = {
            "callsign": "UAL100",
            "plane": "B739",
            "plane_latitude": 37.48,
            "plane_longitude": -122.38,
            "altitude": 5900,
            "data_source": "adsb_fi",
            "icao_hex": "A12345",
        }
        with mock.patch.object(aircraft_alert.geo, "distance_km", return_value=12.0):
            out = aircraft_alert.dedupe_flights([fr24, adsb])
        self.assertEqual(len(out), 2)

    def test_callsign_keys_icao_to_iata(self):
        from utilities.aircraft_alert import callsign_match_keys

        keys = callsign_match_keys("QTR5Q")
        self.assertIn("QTR5Q", keys)
        self.assertIn("QR5Q", keys)

    def test_identity_keys_include_flight_number(self):
        from utilities.aircraft_alert import flights_share_identity

        fr24 = {"callsign": "QTR5Q", "flight_number": "QR737", "icao_hex": ""}
        adsb = {"callsign": "QR737", "icao_hex": ""}
        self.assertTrue(flights_share_identity(fr24, adsb))


if __name__ == "__main__":
    unittest.main()
