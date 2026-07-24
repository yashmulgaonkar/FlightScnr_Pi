"""Tests for NIFC WFIGS wildfire parsing and USA/Canada routing."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DATA_DIR = tempfile.mkdtemp(prefix="flightscnr-wfigs-")
os.environ["FLIGHTSCNR_DATA_DIR"] = _DATA_DIR
os.environ.setdefault("HOME_LAT", "40.7")
os.environ.setdefault("HOME_LON", "-74.0")


_SAMPLE_FEATURES = [
    {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [-74.05, 40.72]},
        "properties": {
            "IncidentName": "Hudson Fire",
            "POOState": "US-NY",
            "POOCounty": "New York",
            "IncidentSize": 120.5,
            "PercentContained": 40.0,
            "FireDiscoveryDateTime": 1721692800000,
            "POOCity": None,
            "UniqueFireIdentifier": "2024-NY-HUDSON",
            "IncidentShortDescription": "Near Midtown",
            "POOProtectingAgency": "NYSDEC",
            "IrwinID": "abc-1",
        },
    },
    {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [-120.3, 40.6]},
        "properties": {
            "IncidentName": "Far West Fire",
            "POOState": "US-CA",
            "POOCounty": "Lassen",
            "IncidentSize": 500.0,
            "PercentContained": 10.0,
            "FireDiscoveryDateTime": 1721606400000,
            "UniqueFireIdentifier": "2024-CA-FAR",
        },
    },
]


class TestWfigsOverlay(unittest.TestCase):
    def test_parse_keeps_nearby(self):
        from display.round_touch.wfigs_overlay import parse_features

        fires = parse_features(
            _SAMPLE_FEATURES, center_lat=40.7, center_lon=-74.0, radius_km=50.0
        )
        self.assertEqual(len(fires), 1)
        fire = fires[0]
        self.assertEqual(fire["source"], "wfigs")
        self.assertEqual(fire["name"], "Hudson Fire")
        self.assertEqual(fire["county"], "New York")
        self.assertEqual(fire["acres"], 120.5)
        self.assertEqual(fire["containment"], 40.0)
        self.assertEqual(fire["started"], "2024-07-23")
        self.assertEqual(fire["location"], "Near Midtown")
        self.assertEqual(fire["state"], "NY")

    def test_parse_includes_far_when_radius_large(self):
        from display.round_touch.wfigs_overlay import parse_features

        fires = parse_features(
            _SAMPLE_FEATURES, center_lat=40.7, center_lon=-74.0, radius_km=5000.0
        )
        names = {f["name"] for f in fires}
        self.assertEqual(names, {"Hudson Fire", "Far West Fire"})

    def test_format_discovery_epoch_ms(self):
        from display.round_touch.wfigs_overlay import _format_discovery

        self.assertEqual(_format_discovery(1721692800000), "2024-07-23")
        self.assertEqual(_format_discovery("2024-07-21T12:00:00Z"), "2024-07-21")

    def test_in_usa_or_canada(self):
        from display.round_touch.wildfire_overlay import in_usa_or_canada

        self.assertTrue(in_usa_or_canada(40.7, -74.0))  # NYC
        self.assertTrue(in_usa_or_canada(45.5, -73.6))  # Montreal
        self.assertTrue(in_usa_or_canada(61.2, -149.9))  # Anchorage
        self.assertTrue(in_usa_or_canada(21.3, -157.8))  # Honolulu
        self.assertFalse(in_usa_or_canada(51.5, -0.1))  # London
        self.assertFalse(in_usa_or_canada(-33.9, 151.2))  # Sydney
        self.assertFalse(in_usa_or_canada(19.4, -99.1))  # Mexico City

    def test_wildfire_router_uses_wfigs_in_us(self):
        from display.round_touch import wildfire_overlay

        with mock.patch.object(wildfire_overlay, "using_calfire", return_value=False):
            with mock.patch.object(wildfire_overlay, "using_wfigs", return_value=True):
                with mock.patch.object(
                    wildfire_overlay.wfigs_overlay,
                    "get_fires",
                    return_value=[{"name": "Hudson Fire", "source": "wfigs"}],
                ):
                    with mock.patch.object(
                        wildfire_overlay.firms_overlay, "get_fires"
                    ) as firms_get:
                        fires = wildfire_overlay.get_fires()
                        firms_get.assert_not_called()
        self.assertEqual(fires[0]["name"], "Hudson Fire")

    def test_wildfire_router_uses_firms_outside_us_canada(self):
        from display.round_touch import wildfire_overlay

        with mock.patch.object(wildfire_overlay, "using_calfire", return_value=False):
            with mock.patch.object(wildfire_overlay, "using_wfigs", return_value=False):
                with mock.patch.object(
                    wildfire_overlay.firms_overlay,
                    "get_fires",
                    return_value=[{"name": "Hotspot", "source": "firms"}],
                ):
                    with mock.patch.object(
                        wildfire_overlay.wfigs_overlay, "get_fires"
                    ) as wfigs_get:
                        fires = wildfire_overlay.get_fires()
                        wfigs_get.assert_not_called()
        self.assertEqual(fires[0]["source"], "firms")

    def test_wildfire_router_skips_wfigs_refresh_in_ca(self):
        from display.round_touch import wildfire_overlay

        with mock.patch.object(wildfire_overlay, "using_calfire", return_value=True):
            with mock.patch.object(
                wildfire_overlay.calfire_overlay, "request_refresh"
            ) as cf:
                with mock.patch.object(
                    wildfire_overlay.wfigs_overlay, "request_refresh"
                ) as wfigs:
                    with mock.patch.object(
                        wildfire_overlay.firms_overlay, "request_refresh"
                    ) as firms:
                        with mock.patch.object(
                            wildfire_overlay.wfigs_overlay, "invalidate"
                        ) as inv_w:
                            with mock.patch.object(
                                wildfire_overlay.firms_overlay, "invalidate"
                            ) as inv_f:
                                wildfire_overlay.request_refresh(force=True)
                                cf.assert_called_once_with(force=True)
                                wfigs.assert_not_called()
                                firms.assert_not_called()
                                inv_w.assert_called_once()
                                inv_f.assert_called_once()


if __name__ == "__main__":
    unittest.main()
