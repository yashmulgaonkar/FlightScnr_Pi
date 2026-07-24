"""Tests for radar trail history buffer."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DATA_DIR = tempfile.mkdtemp(prefix="flightscnr-trail-")
os.environ["FLIGHTSCNR_DATA_DIR"] = _DATA_DIR


class TestTrailHistory(unittest.TestCase):
    def test_observe_and_cap(self):
        from display.round_touch.trail_history import TrailHistory

        hist = TrailHistory(max_points=5, min_move_km=0.01)
        now = 1_000_000.0
        for i in range(8):
            hist.observe(
                {
                    "icao_hex": "ABC123",
                    "plane_latitude": 37.0 + i * 0.01,
                    "plane_longitude": -122.0,
                },
                now=now + i,
            )
        trail = hist.local_trail("hex:ABC123", now=now + 8)
        self.assertEqual(len(trail), 5)
        self.assertAlmostEqual(trail[0][0], 37.03, places=4)
        self.assertAlmostEqual(trail[-1][0], 37.07, places=4)

    def test_ignores_tiny_moves(self):
        from display.round_touch.trail_history import TrailHistory

        hist = TrailHistory(min_move_km=0.5)
        hist.observe(
            {"icao_hex": "X", "plane_latitude": 37.0, "plane_longitude": -122.0},
            now=1.0,
        )
        hist.observe(
            {"icao_hex": "X", "plane_latitude": 37.0001, "plane_longitude": -122.0},
            now=2.0,
        )
        self.assertEqual(len(hist.local_trail("hex:X", now=2.0)), 1)

    def test_merge_fr24_trail(self):
        from display.round_touch.trail_history import TrailHistory, normalize_fr24_trail

        # Newest-first as stored by overhead / used by web map (reversed for draw).
        raw = [[37.4, -122.1], [37.3, -122.1], [37.2, -122.1]]
        chrono = normalize_fr24_trail(raw)
        self.assertAlmostEqual(chrono[0][0], 37.2, places=4)
        self.assertAlmostEqual(chrono[-1][0], 37.4, places=4)

        hist = TrailHistory(min_move_km=0.01)
        hist.observe(
            {"icao_hex": "Y", "plane_latitude": 37.45, "plane_longitude": -122.1},
            now=10.0,
        )
        merged = hist.trail_for_flight(
            {
                "icao_hex": "Y",
                "plane_latitude": 37.45,
                "plane_longitude": -122.1,
                "trail": raw,
            },
            now=10.0,
        )
        self.assertGreaterEqual(len(merged), 3)
        self.assertAlmostEqual(merged[-1][0], 37.45, places=4)

    def test_trail_screen_points_helper(self):
        from unittest import mock

        from display.round_touch.screens import radar

        with mock.patch.object(radar.geo, "inner_ring_max_km", return_value=50.0):
            with mock.patch.object(
                radar.geo,
                "local_offset_km",
                side_effect=lambda lat, lon: (0, 0, abs(lat - 37.0) * 111),
            ):
                with mock.patch.object(
                    radar.geo,
                    "lat_lon_to_screen",
                    side_effect=lambda lat, lon: (int(lat * 10), int(abs(lon) * 10)),
                ):
                    pts = radar._trail_screen_points(
                        [
                            (37.0, -122.0),
                            (37.1, -122.0),
                            (40.0, -122.0),  # ~333 km — outside 50 km ring
                        ]
                    )
        self.assertEqual(len(pts), 2)
        self.assertEqual(pts[0], (370, 1220))


if __name__ == "__main__":
    unittest.main()
