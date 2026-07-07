"""Round 720×720 touch display — FlightScnr UI."""

import logging
import math
import os
import time
from threading import Thread

import pygame

from utilities.overhead import Overhead
from utilities.route_enrichment import (
    fetch_route_enrichment,
    lookup_callsign,
    merge_route_enrichment,
    needs_route_enrichment,
)
from display.round_touch import (
    draw,
    ghost_touch_filter,
    gesture_handler,
    input_handler,
    map_bg,
    nav,
    pinch_handler,
    rotation,
    scale,
    settings,
    theme,
    touch_debug,
    video,
)
from utilities import aircraft_alert
from display.round_touch.screens import (
    clock,
    clock_settings,
    details,
    flight_detail,
    forecast,
    info,
    radar,
    tracked,
)

logger = logging.getLogger("flightscnr.display")

SCREEN_RADAR = "radar"
SCREEN_FLIGHT = "flight_detail"
SCREEN_SETTINGS = "settings"
SCREEN_DETAILS = "details"
SCREEN_CLOCK = "clock"
SCREEN_CLOCK_SETTINGS = "clock_settings"
SCREEN_FORECAST = "forecast"
SCREEN_TRACKED = "tracked"

SECONDARY_TIMEOUT_S = 45
BOOT_SPLASH_S = 3
AUTO_IDLE_MIN_RADAR_S = 5
OFF_HOURS_TOUCH_WAKE_S = 300


class RoundTouchDisplay:
    def __init__(self):
        try:
            from config import DISPLAY_FULLSCREEN
            fullscreen = DISPLAY_FULLSCREEN
        except ImportError:
            fullscreen = os.environ.get("DISPLAY_FULLSCREEN", "true").lower() in ("1", "true", "yes")

        requested = theme.SIZE
        self._display = video.init_display(requested, requested, fullscreen)
        fit_side = min(self._display.get_size())
        if fit_side != theme.SIZE:
            logger.info(
                "Framebuffer adjusted %d×%d → %d×%d to match display",
                requested,
                requested,
                fit_side,
                fit_side,
            )
            theme.set_framebuffer_side(fit_side)
            map_bg.invalidate()
            if self._display.get_size() != (fit_side, fit_side):
                pygame.display.quit()
                self._display = video.init_display(fit_side, fit_side, fullscreen)
        self.surface = pygame.Surface((theme.SIZE, theme.SIZE))
        pygame.mouse.set_visible(False)
        pygame.event.set_allowed(
            None
        )  # allow all; we filter QUIT manually

        scale.select(settings.scale_index())
        settings.apply_theme_colors()

        self.overhead = Overhead()
        self.overhead.grab_data()

        self.input = input_handler.TouchInput()
        self.pinch = pinch_handler.PinchZoom()
        self.gestures = gesture_handler.RadarGestureHandler(self.input, self.pinch)
        self._ghost_filter = ghost_touch_filter.GhostTouchFilter()
        self.screen = SCREEN_RADAR
        self.settings_page = info.PAGE_MAIN
        self.flights = []
        self.flight_index = 0
        self._secondary_activity = time.time()
        self._boot_until = time.time() + BOOT_SPLASH_S
        self._last_clock_minute = -1
        self._last_clock_draw = 0.0
        self._last_radar_draw = 0
        self._last_static_draw = 0
        self._display_focus = 0
        self._fatal_error = None
        self._scroll = nav.ScrollState()
        self._last_grab_seq = 0
        self._radar_visible_since = time.time()
        self._auto_idle_clock = False
        self._weather_redraw_pending = False
        self._route_enrichment: dict[str, dict] = {}
        self._route_enrich_inflight: set[str] = set()
        self._route_enrich_redraw = False
        self._last_settings_reload = 0.0
        self._off_hours_wake_until = 0.0

        radar._init_sweep()
        map_bg.request_background()
        map_bg.prewarm_all_scales()
        self._apply_brightness()
        if settings.auto_timezone_enabled():
            try:
                from config import LOCATION_HOME, location_configured
                from utilities.tz_lookup import maybe_apply_auto_timezone

                if location_configured():
                    maybe_apply_auto_timezone(LOCATION_HOME[0], LOCATION_HOME[1])
            except ImportError:
                pass
        self._safe_draw()

    def _refresh_flights(self):
        try:
            scale.select(settings.scale_index())
            if self.overhead.processing:
                return
            self.flights = self.overhead.peek_data()
        except Exception:
            logger.exception("Failed to refresh flight data")

    def _ordered_flights(self):
        return radar.flights_by_distance(self.flights)

    def _present(self):
        rotation.present(self._display, self.surface)
        pygame.display.flip()

    def _draw(self):
        if self._fatal_error:
            draw.draw_error(self.surface, self._fatal_error)
            draw.apply_round_bezel(self.surface)
            self._present()
            return

        if time.time() < self._boot_until:
            details.draw_details(self.surface, boot_splash=True)
            draw.apply_round_bezel(self.surface)
            self._present()
            return

        if self.screen == SCREEN_RADAR:
            radar.draw_radar(self.surface, self.flights)
        elif self.screen == SCREEN_FLIGHT:
            self._scroll.max_offset = flight_detail.draw_flight_detail(
                self.surface,
                self._flights_for_detail(),
                self.flight_index,
                self._scroll.offset,
            )
        elif self.screen == SCREEN_SETTINGS:
            self._scroll.max_offset = info.draw_info(
                self.surface,
                self.settings_page,
                self._scroll.offset,
                self._display_focus,
            )
        elif self.screen == SCREEN_DETAILS:
            self._scroll.max_offset = details.draw_details(self.surface, scroll_offset=self._scroll.offset)
        elif self.screen == SCREEN_CLOCK:
            clock.draw_clock(self.surface)
        elif self.screen == SCREEN_CLOCK_SETTINGS:
            clock_settings.draw_clock_settings(self.surface)
        elif self.screen == SCREEN_FORECAST:
            forecast.draw_forecast(self.surface)
        elif self.screen == SCREEN_TRACKED:
            if not self.overhead.processing:
                self._refresh_flights()
            display_data = tracked.resolve_display_data(
                self.overhead.tracked_data,
                self.flights,
            )
            self._scroll.max_offset = tracked.draw_tracked(
                self.surface,
                display_data,
                scroll_offset=self._scroll.offset,
            )
        self._scroll.clamp()
        remaining = self._timeout_remaining_fraction()
        if remaining is not None:
            draw.draw_timeout_ring(self.surface, remaining)
        draw.apply_round_bezel(self.surface)
        self._present()

    def _timeout_remaining_fraction(self) -> float | None:
        """Fraction of secondary-screen timeout remaining, or None if not applicable."""
        if time.time() < self._boot_until:
            return None
        if self.screen in (SCREEN_RADAR, SCREEN_CLOCK, SCREEN_CLOCK_SETTINGS, SCREEN_FORECAST):
            return None
        if self.screen == SCREEN_TRACKED and tracked.is_pinned():
            return None
        elapsed = time.time() - self._secondary_activity
        return max(0.0, (SECONDARY_TIMEOUT_S - elapsed) / SECONDARY_TIMEOUT_S)

    def _safe_draw(self):
        try:
            self._draw()
        except Exception as exc:
            self._fatal_error = str(exc)
            logger.exception("Display draw failed")
            try:
                draw.draw_error(self.surface, self._fatal_error)
                draw.apply_round_bezel(self.surface)
                self._present()
            except Exception:
                logger.exception("Could not render error screen")

    def _note_activity(self):
        self._secondary_activity = time.time()

    def _idle_clock_holds_screen(self) -> bool:
        """Auto-idle clock should keep clock up while no in-range aircraft."""
        return (
            self._auto_idle_clock
            and settings.auto_idle_clock_enabled()
            and self.screen in (SCREEN_CLOCK, SCREEN_CLOCK_SETTINGS, SCREEN_FORECAST)
            and radar.visible_in_range_count(self.flights) == 0
        )

    def _return_to_radar(self):
        self._fatal_error = None
        if self.screen == SCREEN_TRACKED:
            tracked.reset_marquee()
        self._radar_visible_since = time.time()
        self._auto_idle_clock = False
        self.screen = SCREEN_RADAR
        self.settings_page = info.PAGE_MAIN
        self._scroll.reset()

    def _set_settings_page(self, page: int):
        if page != self.settings_page:
            self._scroll.reset()
            if page != info.PAGE_DISPLAY:
                self._display_focus = 0
        self.settings_page = page

    def _open_screen(self, screen: str):
        if screen == SCREEN_CLOCK:
            self._last_clock_minute = -1
            self._last_clock_draw = 0.0
        if screen != self.screen:
            if self.screen == SCREEN_TRACKED:
                tracked.reset_marquee()
            self._scroll.reset()
        if screen == SCREEN_RADAR:
            self._radar_visible_since = time.time()
            self._auto_idle_clock = False
        else:
            # Reset secondary timeout window when entering any non-radar screen.
            # Without this, a stale timestamp can immediately bounce back to radar.
            self._note_activity()
        self.screen = screen
        if screen == SCREEN_CLOCK:
            self._safe_draw()

    def _apply_display_row(self, row: int):
        self._display_focus = row
        if row == 0:
            pct = settings.brightness_percent() + 5
            if pct > 100:
                pct = 10
            settings.set_brightness_percent(pct)
            self._apply_brightness()
        elif row == 1:
            settings.toggle_distance_units()
        elif row == 2:
            settings.cycle_scale()
            scale.select(settings.scale_index())
            map_bg.request_background()
        elif row == 3:
            settings.toggle_compass_rose()
        elif row == 4:
            settings.cycle_min_height()
        elif row == 5:
            settings.toggle_sweep_line()
        elif row == 6:
            settings.toggle_auto_idle_clock()

    def _apply_brightness(self):
        from display.round_touch import backlight, off_hours

        pct = off_hours.effective_brightness_percent(settings.brightness_percent())
        if pct == 0 and time.time() < self._off_hours_wake_until:
            pct = settings.brightness_percent()
        backlight.apply_percent(pct)

    def _wake_for_off_hours_touch(self):
        from display.round_touch import off_hours

        if not off_hours.in_off_hours():
            return
        if off_hours.effective_brightness_percent(settings.brightness_percent()) != 0:
            return
        self._off_hours_wake_until = time.time() + OFF_HOURS_TOUCH_WAKE_S
        # Only force-open clock for explicit off-hours clock mode.
        # In "turn off display" mode we should keep current screen so radar taps
        # (e.g. selecting aircraft) work normally after wake.
        if off_hours.force_clock_enabled() and self.screen == SCREEN_RADAR:
            self._open_screen(SCREEN_CLOCK)
        self._safe_draw()

    def _note_off_hours_override(self):
        from display.round_touch import off_hours

        if not off_hours.in_off_hours():
            return
        # Temporary wake override is only for "turn off display" mode.
        if off_hours.effective_brightness_percent(settings.brightness_percent()) == 0:
            self._off_hours_wake_until = time.time() + OFF_HOURS_TOUCH_WAKE_S

    def _apply_scale_step(self, delta: int):
        """delta: -1 closer range, +1 wider range."""
        idx = settings.scale_index()
        new_idx = max(0, min(len(scale.SCALE_BANDS) - 1, idx + delta))
        if new_idx == idx:
            return
        settings.set_scale_index(new_idx)
        scale.select(new_idx)
        map_bg.request_background()
        self._safe_draw()

    def _flights_for_detail(self):
        ordered = self._ordered_flights()
        return [merge_route_enrichment(f, self._route_enrichment) for f in ordered]

    def _maybe_enrich_flight_detail(self):
        """Fetch AirLabs route data for the open flight detail row (background)."""
        if self.screen != SCREEN_FLIGHT:
            return
        ordered = self._ordered_flights()
        if not ordered:
            return
        idx = max(0, min(self.flight_index, len(ordered) - 1))
        flight = ordered[idx]
        if not needs_route_enrichment(flight):
            return
        callsign = lookup_callsign(flight)
        if not callsign or callsign in self._route_enrichment:
            return
        if callsign in self._route_enrich_inflight:
            return
        self._route_enrich_inflight.add(callsign)

        def _work():
            try:
                enrichment = fetch_route_enrichment(flight)
                if enrichment:
                    self._route_enrichment[callsign] = enrichment
                    self._route_enrich_redraw = True
            finally:
                self._route_enrich_inflight.discard(callsign)

        Thread(target=_work, daemon=True).start()

    def _open_flight_at(self, x: int, y: int, alt_x: int | None = None, alt_y: int | None = None) -> bool:
        picked = radar.pick_flight_at(self.flights, x, y, alt_x, alt_y)
        ordered = self._ordered_flights()
        if not picked or not ordered:
            return False
        try:
            self.flight_index = ordered.index(picked)
        except ValueError:
            self.flight_index = 0
        self._open_screen(SCREEN_FLIGHT)
        self._note_activity()
        self._maybe_enrich_flight_detail()
        return True

    def _apply_scroll_delta(self, delta: int):
        if not delta:
            return
        self._scroll.step(delta)
        self._note_activity()
        self._safe_draw()

    def _handle_scroll_drag(self):
        dy = self.input.consume_scroll_drag()
        if not dy:
            return
        if self.screen == SCREEN_FLIGHT:
            self._apply_scroll_delta(-dy)
        elif self.screen == SCREEN_DETAILS:
            self._apply_scroll_delta(-dy)

    def _handle_settings_tap(self, x: int | None = None, y: int | None = None):
        if self.settings_page == info.PAGE_DISPLAY and x is not None and y is not None:
            row = info.display_row_at(x, y)
            if row is not None:
                self._apply_display_row(row)
        elif self.settings_page == info.PAGE_COLORS and x is not None and y is not None:
            row = info.theme_row_at(x, y, self._scroll.offset)
            if row is not None:
                settings.set_theme_index(row)

    def _handle_navigation(self):
        if time.time() < self._boot_until:
            return

        self._handle_scroll_drag()

        gesture = self.input.consume_gesture()
        if self._fatal_error and gesture:
            kind = gesture[0]
            if kind == "swipe" or kind == "tap":
                self._return_to_radar()
                self._safe_draw()
                return
        swipe = input_handler.SWIPE_NONE
        swipe_end = None
        swipe_start = None
        tap = None
        if gesture:
            kind = gesture[0]
            if kind == "swipe":
                swipe = gesture[1]
                swipe_end = gesture[2] if len(gesture) > 2 else None
                swipe_start = gesture[3] if len(gesture) > 3 else None
            else:
                tap = gesture[1]

        if swipe != input_handler.SWIPE_NONE and self.screen not in (
            SCREEN_RADAR, SCREEN_CLOCK, SCREEN_CLOCK_SETTINGS, SCREEN_FORECAST,
        ):
            self._note_activity()

        # Tracked sits left of radar: swipe right on radar opens it; swipe left returns.
        if swipe == input_handler.SWIPE_RIGHT and self.screen == SCREEN_RADAR:
            travel = 0.0
            if swipe_start and swipe_end:
                travel = math.hypot(
                    swipe_end[0] - swipe_start[0],
                    swipe_end[1] - swipe_start[1],
                )
            threshold = input_handler.gesture_threshold_px()
            opened = False
            if travel >= threshold:
                self._open_screen(SCREEN_TRACKED)
                self._scroll.reset()
                self._note_activity()
                self._safe_draw()
            else:
                if swipe_end:
                    opened = self._open_flight_at(swipe_end[0], swipe_end[1])
                if not opened and swipe_start and swipe_end:
                    opened = self._open_flight_at(
                        swipe_start[0], swipe_start[1], swipe_end[0], swipe_end[1],
                    )
                elif not opened and swipe_start:
                    opened = self._open_flight_at(swipe_start[0], swipe_start[1])
                if opened:
                    self._safe_draw()
        elif swipe == input_handler.SWIPE_LEFT and self.screen == SCREEN_TRACKED:
            self._return_to_radar()
            self._safe_draw()
        elif swipe == input_handler.SWIPE_DOWN and self.screen == SCREEN_RADAR:
            self._open_screen(SCREEN_CLOCK)
            self._auto_idle_clock = False
            self._safe_draw()
        elif swipe == input_handler.SWIPE_LEFT and self.screen == SCREEN_CLOCK:
            self._open_screen(SCREEN_CLOCK_SETTINGS)
            self._safe_draw()
        elif swipe == input_handler.SWIPE_RIGHT and self.screen == SCREEN_CLOCK_SETTINGS:
            self._open_screen(SCREEN_CLOCK)
            self._safe_draw()
        elif swipe == input_handler.SWIPE_RIGHT and self.screen == SCREEN_CLOCK:
            self._open_screen(SCREEN_FORECAST)
            self._safe_draw()
        elif swipe == input_handler.SWIPE_LEFT and self.screen == SCREEN_FORECAST:
            self._open_screen(SCREEN_CLOCK)
            self._safe_draw()
        elif swipe == input_handler.SWIPE_UP and self.screen == SCREEN_FORECAST:
            self._return_to_radar()
            self._safe_draw()
        elif swipe == input_handler.SWIPE_UP and self.screen == SCREEN_RADAR:
            self._open_screen(SCREEN_DETAILS)
            self._note_activity()
            self._safe_draw()
        elif swipe == input_handler.SWIPE_DOWN and self.screen == SCREEN_DETAILS:
            self._return_to_radar()
            self._safe_draw()
        elif swipe == input_handler.SWIPE_UP and self.screen == SCREEN_CLOCK:
            self._return_to_radar()
            self._safe_draw()
        elif swipe == input_handler.SWIPE_LEFT and self.screen == SCREEN_RADAR:
            self._open_screen(SCREEN_SETTINGS)
            self.settings_page = info.PAGE_MAIN
            self._note_activity()
            self._safe_draw()
        elif self.screen == SCREEN_FLIGHT and swipe in (input_handler.SWIPE_UP, input_handler.SWIPE_DOWN):
            delta = -nav.scroll_step() if swipe == input_handler.SWIPE_UP else nav.scroll_step()
            self._scroll.step(delta)
            self._safe_draw()
        elif swipe in (input_handler.SWIPE_UP, input_handler.SWIPE_DOWN) and self.screen == SCREEN_DETAILS:
            delta = -nav.scroll_step() if swipe == input_handler.SWIPE_UP else nav.scroll_step()
            self._scroll.step(delta)
            self._safe_draw()
        if tap and not theme.in_visible_circle(tap[0], tap[1]):
            tap = None
        if tap and nav.tap_breadcrumb(tap[0], tap[1]) and self.screen != SCREEN_RADAR:
            if self.screen == SCREEN_TRACKED:
                self._return_to_radar()
            elif self.screen == SCREEN_FORECAST:
                self._open_screen(SCREEN_CLOCK)
            elif self.screen == SCREEN_CLOCK_SETTINGS:
                self._open_screen(SCREEN_CLOCK)
            elif self.screen == SCREEN_SETTINGS and self.settings_page == info.PAGE_COLORS:
                self._set_settings_page(info.PAGE_DISPLAY)
            elif self.screen == SCREEN_SETTINGS and self.settings_page == info.PAGE_DISPLAY:
                self._set_settings_page(info.PAGE_MAIN)
            else:
                self._return_to_radar()
            self._note_activity()
            self._safe_draw()
        elif tap and self.screen == SCREEN_RADAR:
            if self.pinch.should_suppress_tap():
                tap = None
            if tap and self._open_flight_at(tap[0], tap[1]):
                self._safe_draw()
        elif tap and self.screen == SCREEN_FLIGHT:
            ordered = self._ordered_flights()
            action = flight_detail.tap_footer_action(tap[0], tap[1], ordered)
            if action == "prev" and ordered:
                self.flight_index = (self.flight_index - 1) % len(ordered)
                self._scroll.reset()
                self._note_activity()
                self._maybe_enrich_flight_detail()
                self._safe_draw()
            elif action == "next" and ordered:
                self.flight_index = (self.flight_index + 1) % len(ordered)
                self._scroll.reset()
                self._note_activity()
                self._maybe_enrich_flight_detail()
                self._safe_draw()
            elif action == "radar":
                self._return_to_radar()
                self._safe_draw()
        elif tap and self.screen == SCREEN_TRACKED:
            action = tracked.tap_footer_action(
                tap[0], tap[1], self.overhead.tracked_data
            )
            if action == "pin":
                tracked.toggle_pinned()
                self._note_activity()
                self._safe_draw()
            elif action == "radar":
                tracked.clear_pinned()
                self._return_to_radar()
                self._safe_draw()
        elif tap and self.screen == SCREEN_CLOCK_SETTINGS:
            row = clock_settings.row_at(tap[0], tap[1])
            if row is not None:
                clock_settings.apply_row(row)
            action = clock_settings.tap_footer_action(tap[0], tap[1])
            if action == "radar":
                self._return_to_radar()
            self._safe_draw()
        elif tap and self.screen == SCREEN_CLOCK:
            action = clock.tap_footer_action(tap[0], tap[1])
            if action == "radar":
                self._return_to_radar()
                self._safe_draw()
            elif clock.tap_on_time(tap[0], tap[1]):
                settings.toggle_clock_format()
                self._note_activity()
                self._safe_draw()
        elif tap and self.screen == SCREEN_FORECAST:
            action = forecast.tap_footer_action(tap[0], tap[1])
            if action == "radar":
                self._return_to_radar()
                self._safe_draw()
        elif tap and self.screen == SCREEN_DETAILS:
            action = details.tap_footer_action(tap[0], tap[1])
            if action == "radar":
                self._return_to_radar()
                self._safe_draw()
        elif tap and self.screen == SCREEN_SETTINGS:
            action = info.tap_footer_action(tap[0], tap[1])
            if action == "prev":
                prev = info.prev_page(self.settings_page)
                if prev is not None:
                    self._set_settings_page(prev)
            elif action == "next":
                nxt = info.next_page(self.settings_page)
                if nxt is not None:
                    self._set_settings_page(nxt)
            elif action == "radar":
                self._return_to_radar()
            else:
                self._handle_settings_tap(tap[0], tap[1])
            self._note_activity()
            self._safe_draw()

    def _tick_timeout(self):
        if time.time() < self._boot_until:
            return
        from display.round_touch import off_hours

        # In off-hours clock mode, keep clock/forecast screens stable instead of
        # timing out back to radar (prevents clock<->radar flicker).
        if (
            self.screen in (SCREEN_CLOCK, SCREEN_CLOCK_SETTINGS, SCREEN_FORECAST)
            and off_hours.in_off_hours()
            and off_hours.force_clock_enabled()
        ):
            return
        if self._idle_clock_holds_screen():
            return
        if self.screen == SCREEN_RADAR:
            return
        if self.screen == SCREEN_TRACKED and tracked.is_pinned():
            return

        timeout_s = SECONDARY_TIMEOUT_S
        if self.screen == SCREEN_FLIGHT:
            timeout_s = settings.flight_detail_timeout_s()
        elif self.screen in (SCREEN_CLOCK, SCREEN_CLOCK_SETTINGS, SCREEN_FORECAST):
            timeout_s = settings.clock_timeout_s()

        if timeout_s <= 0:
            return

        if time.time() - self._secondary_activity >= timeout_s:
            self._return_to_radar()
            self._safe_draw()

    def _tick_clock(self):
        if self.screen not in (SCREEN_CLOCK, SCREEN_CLOCK_SETTINGS, SCREEN_FORECAST):
            return
        now = time.time()
        minute = time.localtime().tm_min + time.localtime().tm_hour * 60
        if (
            minute != self._last_clock_minute
            or (now - self._last_clock_draw) >= 2.0
        ):
            self._last_clock_minute = minute
            self._last_clock_draw = now
            self._safe_draw()

    def _tick_auto_idle_clock(self):
        if not settings.auto_idle_clock_enabled():
            return
        if time.time() < self._boot_until:
            return
        if self.screen == SCREEN_RADAR:
            if radar.visible_in_range_count(self.flights) == 0:
                if time.time() - self._radar_visible_since >= AUTO_IDLE_MIN_RADAR_S:
                    self._auto_idle_clock = True
                    self._open_screen(SCREEN_CLOCK)
                    self._safe_draw()
            else:
                self._radar_visible_since = time.time()
        elif (
            self._auto_idle_clock
            and self.screen in (SCREEN_CLOCK, SCREEN_CLOCK_SETTINGS, SCREEN_FORECAST)
            and radar.visible_in_range_count(self.flights) > 0
        ):
            self._return_to_radar()
            self._safe_draw()

    def _tick_off_hours_clock(self):
        from display.round_touch import off_hours

        if time.time() < self._boot_until:
            return
        if not off_hours.in_off_hours() or not off_hours.force_clock_enabled():
            return
        if self.screen not in (SCREEN_CLOCK, SCREEN_CLOCK_SETTINGS, SCREEN_FORECAST):
            self._open_screen(SCREEN_CLOCK)
            self._safe_draw()

    def _apply_reloaded_settings(self):
        """Apply settings written by another process (e.g. web portal)."""
        scale.select(settings.scale_index())
        map_bg.request_background()
        self._apply_brightness()
        self._safe_draw()

    def _maybe_reload_location(self):
        try:
            from config import reload_location_override
            from display.round_touch import map_bg, weather_data

            if not reload_location_override():
                return
            map_bg.invalidate()
            map_bg.prewarm_all_scales()
            self.overhead.grab_data()

            def _fetch_weather():
                try:
                    weather_data.refresh_for_location_change()
                except Exception:
                    logger.exception("Weather refresh after location change failed")
                else:
                    self._weather_redraw_pending = True

            Thread(target=_fetch_weather, daemon=True).start()
            self._safe_draw()
        except ImportError:
            pass

    def _tick_data(self):
        try:
            scale.select(settings.scale_index())
            self._refresh_flights()
            if not self.overhead.processing:
                self.overhead.grab_data()
            aircraft_alert.check_new_aircraft(self.flights)
        except Exception:
            logger.exception("Flight data poll failed")

    def run(self):
        logger.info(
            "Round touch display starting (%dx%d framebuffer, rotation=%d°, visible radius=%d)",
            theme.SIZE,
            theme.SIZE,
            rotation.rotation_degrees(),
            theme.VISIBLE_RADIUS,
        )
        touch_debug.log_startup()
        running = True
        last_data_poll = 0
        last_location_check = 0
        try:
            from config import DATA_REFRESH_SECONDS
        except ImportError:
            DATA_REFRESH_SECONDS = 2.0

        try:
            while running:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        # Touch drivers / compositors sometimes emit spurious QUIT.
                        logger.warning("Ignoring pygame QUIT event")
                        continue
                    if event.type == pygame.ACTIVEEVENT and not event.gain:
                        logger.debug("Display lost focus (continuing)")
                        continue
                    if gesture_handler.RadarGestureHandler.is_touch_event(event):
                        if not self._ghost_filter.allow(
                            event,
                            self.gestures.touch.cancel_gesture,
                            self.gestures.touch.is_dragging,
                        ):
                            continue
                        self._note_off_hours_override()
                        if gesture_handler.RadarGestureHandler.is_pointer_down(event) or (
                            input_handler.use_finger_events()
                            and event.type == pygame.FINGERDOWN
                        ):
                            self._wake_for_off_hours_touch()
                        touch_debug.log_event(event)
                        if self.screen == SCREEN_RADAR:
                            if gesture_handler.RadarGestureHandler.is_pointer_down(
                                event
                            ) or (
                                input_handler.use_finger_events()
                                and event.type == pygame.FINGERDOWN
                            ):
                                self.gestures.on_pointer_down()
                            elif gesture_handler.RadarGestureHandler.is_pointer_up(event) or (
                                input_handler.use_finger_events()
                                and event.type == pygame.FINGERUP
                                and int(event.finger_id)
                                == self.gestures.touch.active_finger_id()
                            ):
                                self.gestures.on_pointer_up()
                        self.gestures.handle_input_event(event)
                        if (
                            self.screen == SCREEN_RADAR
                            and gesture_handler.RadarGestureHandler.is_finger_event(event)
                        ):
                            scale_delta = self.gestures.handle_finger_event(event)
                            if scale_delta:
                                self._apply_scale_step(scale_delta)
                        self._handle_navigation()

                now = time.time()
                if now - last_data_poll >= DATA_REFRESH_SECONDS:
                    self._tick_data()
                    last_data_poll = now

                grab_seq = self.overhead.grab_seq
                if grab_seq != self._last_grab_seq:
                    self._last_grab_seq = grab_seq
                    self._refresh_flights()
                    if self.screen == SCREEN_TRACKED:
                        self._safe_draw()
                        self._last_static_draw = now
                    elif self.screen == SCREEN_RADAR:
                        self._safe_draw()
                        self._last_radar_draw = now

                if now - last_location_check >= 2.0:
                    self._maybe_reload_location()
                    last_location_check = now

                if now - self._last_settings_reload >= 0.5:
                    if settings.reload():
                        self._apply_reloaded_settings()
                    self._last_settings_reload = now

                if self._route_enrich_redraw and self.screen == SCREEN_FLIGHT:
                    self._route_enrich_redraw = False
                    self._safe_draw()

                if self._weather_redraw_pending and self.screen in (
                    SCREEN_CLOCK,
                    SCREEN_FORECAST,
                ):
                    self._weather_redraw_pending = False
                    self._safe_draw()

                if self._fatal_error:
                    time.sleep(1.0)
                    continue

                if now < self._boot_until:
                    self._safe_draw()
                    time.sleep(0.05)
                elif self.screen == SCREEN_RADAR:
                    radar.tick_sweep()
                    if (now - self._last_radar_draw) * 1000 >= theme.SWEEP_FRAME_MS:
                        self._safe_draw()
                        self._last_radar_draw = now
                elif self.screen in (SCREEN_CLOCK, SCREEN_CLOCK_SETTINGS, SCREEN_FORECAST):
                    self._tick_clock()
                elif self.screen == SCREEN_TRACKED:
                    tracked.tick_marquee()
                    interval = (
                        theme.SWEEP_FRAME_MS / 1000.0
                        if tracked.marquee_animating()
                        or tracked.live_status_active(
                            self.overhead.tracked_data,
                            self.flights,
                        )
                        else DATA_REFRESH_SECONDS
                    )
                    if self._timeout_remaining_fraction() is not None:
                        interval = min(interval, 0.25)
                    if (now - self._last_static_draw) >= interval:
                        self._safe_draw()
                        self._last_static_draw = now
                elif self.screen in (SCREEN_FLIGHT, SCREEN_SETTINGS, SCREEN_DETAILS):
                    if (now - self._last_static_draw) >= 0.25:
                        self._safe_draw()
                        self._last_static_draw = now

                self._tick_timeout()
                self._tick_auto_idle_clock()
                self._tick_off_hours_clock()
                self._apply_brightness()
                time.sleep(0.01)

        except KeyboardInterrupt:
            logger.info("Display stopped by user")
        except Exception:
            logger.exception("Display loop crashed")
            raise
        finally:
            pygame.quit()
