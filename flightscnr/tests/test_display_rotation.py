"""Tests for display rotation + touch inverse mapping."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DATA_DIR = tempfile.mkdtemp(prefix="flightscnr-rotation-")
os.environ["FLIGHTSCNR_DATA_DIR"] = _DATA_DIR
os.environ.setdefault("HOME_LAT", "51.5")
os.environ.setdefault("HOME_LON", "-0.1")


class TestDisplayRotation(unittest.TestCase):
    def test_normalize_degrees(self):
        from display.round_touch.rotation import normalize_degrees

        self.assertEqual(normalize_degrees(90), 90)
        self.assertEqual(normalize_degrees(450), 90)
        self.assertEqual(normalize_degrees(95), 90)

    def test_to_logical_corners(self):
        from display.round_touch import rotation, theme

        side = theme.SIZE
        cases = {
            0: ((10, 20), (10, 20)),
            90: ((10, 20), (20, side - 1 - 10)),
            180: ((10, 20), (side - 1 - 10, side - 1 - 20)),
            270: ((10, 20), (side - 1 - 20, 10)),
        }
        for deg, (phys, expected) in cases.items():
            with self.subTest(deg=deg):
                with mock.patch.object(rotation, "rotation_degrees", return_value=deg):
                    self.assertEqual(rotation.to_logical(*phys), expected)

    def test_cycle_display_rotation(self):
        from display.round_touch import settings

        settings.set_display_rotation(0)
        self.assertEqual(settings.cycle_display_rotation(), 90)
        self.assertEqual(settings.cycle_display_rotation(), 180)
        self.assertEqual(settings.cycle_display_rotation(), 270)
        self.assertEqual(settings.cycle_display_rotation(), 0)


if __name__ == "__main__":
    unittest.main()
