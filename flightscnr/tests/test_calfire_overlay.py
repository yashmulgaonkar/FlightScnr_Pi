"""Tests for CAL FIRE wildfire parsing and California routing."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DATA_DIR = tempfile.mkdtemp(prefix="flightscnr-calfire-")
os.environ["FLIGHTSCNR_DATA_DIR"] = _DATA_DIR
os.environ.setdefault("HOME_LAT", "37.4757")
os.environ.setdefault("HOME_LON", "-122.2062")


_SAMPLE = [
    {
        "Name": "Little Fire",
        "Final": False,
        "Updated": "2026-07-23T15:00:00Z",
        "Started": "2026-07-21T22:39:00Z",
        "AdminUnit": "CAL FIRE Santa Clara Unit",
        "County": "Alameda",
        "Location": "Vallecitos Road and Little Valley Road, Sunol",
        "AcresBurned": 1007.0,
        "PercentContained": 65.0,
        "Longitude": -121.848817,
        "Latitude": 37.61876,
        "Type": "Wildfire",
        "UniqueId": "little-1",
        "Url": "https://www.fire.ca.gov/incidents/2026/7/21/little-fire/",
        "StartedDateOnly": "2026-07-21",
        "IsActive": True,
        "CalFireIncident": True,
    },
    {
        "Name": "Far Away Fire",
        "Final": False,
        "County": "Lassen",
        "AcresBurned": 500.0,
        "PercentContained": 10.0,
        "Longitude": -120.3,
        "Latitude": 40.6,
        "UniqueId": "far-1",
        "Url": "https://www.fire.ca.gov/incidents/2026/7/18/far/",
        "IsActive": True,
    },
    {
        "Name": "Done Fire",
        "Final": True,
        "County": "Alameda",
        "AcresBurned": 50.0,
        "PercentContained": 100.0,
        "Longitude": -122.2,
        "Latitude": 37.5,
        "UniqueId": "done-1",
        "IsActive": False,
    },
]


class TestCalfireOverlay(unittest.TestCase):
    def test_in_california(self):
        from display.round_touch.calfire_overlay import in_california

        self.assertTrue(in_california(37.48, -122.21))
        self.assertFalse(in_california(40.7, -74.0))
        self.assertFalse(in_california(45.0, -122.0))

    def test_parse_keeps_nearby_active(self):
        from display.round_touch.calfire_overlay import parse_incidents

        fires = parse_incidents(
            _SAMPLE, center_lat=37.48, center_lon=-122.21, radius_km=80.0
        )
        self.assertEqual(len(fires), 1)
        fire = fires[0]
        self.assertEqual(fire["source"], "calfire")
        self.assertEqual(fire["name"], "Little Fire")
        self.assertEqual(fire["county"], "Alameda")
        self.assertEqual(fire["acres"], 1007.0)
        self.assertEqual(fire["containment"], 65.0)
        self.assertIn("fire.ca.gov", fire["url"])

    def test_parse_includes_far_when_radius_large(self):
        from display.round_touch.calfire_overlay import parse_incidents

        fires = parse_incidents(
            _SAMPLE, center_lat=37.48, center_lon=-122.21, radius_km=500.0
        )
        names = {f["name"] for f in fires}
        self.assertEqual(names, {"Little Fire", "Far Away Fire"})

    def test_wildfire_router_uses_calfire_in_ca(self):
        from display.round_touch import wildfire_overlay

        with mock.patch.object(wildfire_overlay, "using_calfire", return_value=True):
            with mock.patch.object(
                wildfire_overlay.calfire_overlay,
                "get_fires",
                return_value=[{"name": "Little Fire", "source": "calfire"}],
            ):
                with mock.patch.object(
                    wildfire_overlay.firms_overlay, "get_fires"
                ) as firms_get:
                    fires = wildfire_overlay.get_fires()
                    firms_get.assert_not_called()
        self.assertEqual(fires[0]["name"], "Little Fire")

    def test_wildfire_router_skips_firms_refresh_in_ca(self):
        from display.round_touch import wildfire_overlay

        with mock.patch.object(wildfire_overlay, "using_calfire", return_value=True):
            with mock.patch.object(
                wildfire_overlay.calfire_overlay, "request_refresh"
            ) as cf:
                with mock.patch.object(
                    wildfire_overlay.firms_overlay, "request_refresh"
                ) as firms:
                    with mock.patch.object(
                        wildfire_overlay.firms_overlay, "invalidate"
                    ) as inv:
                        wildfire_overlay.request_refresh(force=True)
                        cf.assert_called_once_with(force=True)
                        firms.assert_not_called()
                        inv.assert_called_once()


if __name__ == "__main__":
    unittest.main()
