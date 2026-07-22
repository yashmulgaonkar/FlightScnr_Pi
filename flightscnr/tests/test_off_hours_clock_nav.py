"""Off-hours force-clock must not fight deliberate navigation to radar (#18)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from display.round_touch import app as app_mod  # noqa: E402


def _bare_display() -> app_mod.RoundTouchDisplay:
    d = object.__new__(app_mod.RoundTouchDisplay)
    d._boot_until = 0.0
    d._off_hours_force_clock_active = False
    d._off_hours_wake_until = 0.0
    d.screen = app_mod.SCREEN_RADAR
    d._opened: list[str] = []
    d._open_screen = lambda screen: d._opened.append(screen)
    d._safe_draw = lambda: None
    return d


class TestOffHoursClockNav(unittest.TestCase):
    def test_forces_clock_once_when_off_hours_starts(self):
        d = _bare_display()
        with mock.patch(
            "display.round_touch.off_hours.in_off_hours", return_value=True
        ), mock.patch(
            "display.round_touch.off_hours.force_clock_enabled", return_value=True
        ):
            d._tick_off_hours_clock()
        self.assertEqual(d._opened, [app_mod.SCREEN_CLOCK])
        self.assertTrue(d._off_hours_force_clock_active)

    def test_allows_radar_after_user_navigates(self):
        d = _bare_display()
        with mock.patch(
            "display.round_touch.off_hours.in_off_hours", return_value=True
        ), mock.patch(
            "display.round_touch.off_hours.force_clock_enabled", return_value=True
        ):
            d._tick_off_hours_clock()  # enter force-clock → snap once
            d.screen = app_mod.SCREEN_RADAR  # user swipes to radar
            d._tick_off_hours_clock()
            d._tick_off_hours_clock()
        self.assertEqual(d._opened, [app_mod.SCREEN_CLOCK])
        self.assertEqual(d.screen, app_mod.SCREEN_RADAR)

    def test_no_force_when_force_clock_disabled(self):
        d = _bare_display()
        with mock.patch(
            "display.round_touch.off_hours.in_off_hours", return_value=True
        ), mock.patch(
            "display.round_touch.off_hours.force_clock_enabled", return_value=False
        ):
            d._tick_off_hours_clock()
        self.assertEqual(d._opened, [])
        self.assertFalse(d._off_hours_force_clock_active)

    def test_radar_uses_day_brightness_in_off_hours(self):
        d = _bare_display()
        d.screen = app_mod.SCREEN_RADAR
        applied = []

        with mock.patch(
            "display.round_touch.off_hours.in_off_hours", return_value=True
        ), mock.patch(
            "display.round_touch.off_hours.effective_brightness_percent",
            return_value=20,
        ), mock.patch(
            "display.round_touch.settings.brightness_percent", return_value=80
        ), mock.patch(
            "display.round_touch.backlight.apply_percent", side_effect=applied.append
        ):
            d._apply_brightness()
        self.assertEqual(applied, [80])

        d.screen = app_mod.SCREEN_CLOCK
        applied.clear()
        with mock.patch(
            "display.round_touch.off_hours.in_off_hours", return_value=True
        ), mock.patch(
            "display.round_touch.off_hours.effective_brightness_percent",
            return_value=20,
        ), mock.patch(
            "display.round_touch.settings.brightness_percent", return_value=80
        ), mock.patch(
            "display.round_touch.backlight.apply_percent", side_effect=applied.append
        ):
            d._apply_brightness()
        self.assertEqual(applied, [20])


if __name__ == "__main__":
    unittest.main()
