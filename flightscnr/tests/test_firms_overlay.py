"""Tests for NASA FIRMS wildfire CSV parse and bbox helpers."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DATA_DIR = tempfile.mkdtemp(prefix="flightscnr-firms-")
os.environ["FLIGHTSCNR_DATA_DIR"] = _DATA_DIR
os.environ.setdefault("HOME_LAT", "37.4757")
os.environ.setdefault("HOME_LON", "-122.2062")


def _csv_for(now: datetime) -> str:
    fresh = now.astimezone(timezone.utc)
    stale = fresh - timedelta(hours=36)
    fresh_date = fresh.strftime("%Y-%m-%d")
    fresh_time = fresh.strftime("%H%M")
    stale_date = stale.strftime("%Y-%m-%d")
    stale_time = stale.strftime("%H%M")
    return (
        "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
        "instrument,confidence,version,bright_ti5,frp,daynight,type\n"
        f"37.48000,-122.21000,330.1,0.39,0.36,{fresh_date},{fresh_time},N,VIIRS,"
        f"n,2.0NRT,290.2,12.5,D,0\n"
        f"37.49000,-122.22000,340.0,0.39,0.36,{fresh_date},{fresh_time},N,VIIRS,"
        f"h,2.0NRT,295.0,55.0,D,0\n"
        f"37.50000,-122.23000,320.0,0.39,0.36,{fresh_date},{fresh_time},N,VIIRS,"
        f"l,2.0NRT,280.0,2.0,D,0\n"
        f"37.51000,-122.24000,330.0,0.39,0.36,{stale_date},{stale_time},N,VIIRS,"
        f"n,2.0NRT,290.0,10.0,D,0\n"
        f"37.52000,-122.25000,330.0,0.39,0.36,{fresh_date},{fresh_time},N,VIIRS,"
        f"n,2.0NRT,290.0,10.0,D,2\n"
        "bad,row,,,,,,,,,,,,,,\n"
    )


class TestFirmsOverlay(unittest.TestCase):
    def test_parse_keeps_only_active_fires(self):
        from display.round_touch.firms_overlay import parse_firms_csv

        now = datetime(2026, 7, 23, 22, 0, tzinfo=timezone.utc)
        fires = parse_firms_csv(_csv_for(now), now=now.timestamp())
        self.assertEqual(len(fires), 2)
        self.assertAlmostEqual(fires[0]["lat"], 37.48)
        self.assertEqual(fires[0]["confidence"], "n")
        self.assertAlmostEqual(fires[1]["lat"], 37.49)
        self.assertEqual(fires[1]["confidence"], "h")

    def test_parse_empty_or_error(self):
        from display.round_touch.firms_overlay import parse_firms_csv

        self.assertEqual(parse_firms_csv(""), [])
        self.assertEqual(parse_firms_csv("Invalid MAP_KEY"), [])

    def test_bbox_from_center(self):
        from display.round_touch.firms_overlay import bbox_from_center

        west, south, east, north = bbox_from_center(37.5, -122.2, 10.0, margin=1.0)
        self.assertLess(west, -122.2)
        self.assertGreater(east, -122.2)
        self.assertLess(south, 37.5)
        self.assertGreater(north, 37.5)

    def test_request_refresh_skips_without_key(self):
        from display.round_touch import firms_overlay

        with mock.patch.object(firms_overlay, "_map_key", return_value=""):
            with mock.patch.object(firms_overlay, "_enabled", return_value=False):
                with mock.patch("requests.get") as get:
                    firms_overlay.request_refresh(force=True)
                    get.assert_not_called()

    def test_fetch_uses_parsed_csv(self):
        from display.round_touch import firms_overlay

        now = datetime(2026, 7, 23, 22, 0, tzinfo=timezone.utc)

        class Resp:
            text = _csv_for(now)

            def raise_for_status(self):
                return None

        with mock.patch("requests.get", return_value=Resp()) as get:
            with mock.patch.object(firms_overlay.time, "time", return_value=now.timestamp()):
                fires = firms_overlay.fetch_fires_for_center(
                    37.5, -122.2, 20.0, map_key="test-key"
                )
        self.assertEqual(len(fires), 2)
        get.assert_called_once()
        url = get.call_args[0][0]
        self.assertIn("test-key", url)
        self.assertIn(firms_overlay.SOURCE, url)

    def test_basemap_screen_matches_home(self):
        from display.round_touch import map_bg, theme

        pos = map_bg.lat_lon_to_basemap_screen(37.4757, -122.2062)
        self.assertIsNotNone(pos)
        self.assertEqual(pos, (theme.CENTER_X, theme.CENTER_Y))


if __name__ == "__main__":
    unittest.main()
