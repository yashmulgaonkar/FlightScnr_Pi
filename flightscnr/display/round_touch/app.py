"""Round 1080×1080 touch display — FlightScnr UI."""

import logging
import os
import time

import pygame

from utilities.overhead import Overhead
from display.round_touch import draw, input_handler, map_bg, nav, scale, settings, theme, video
from display.round_touch.screens import clock, details, flight_detail, info, radar, tracked

logger = logging.getLogger("flightscnr.display")

SCREEN_RADAR = "radar"
SCREEN_FLIGHT = "flight_detail"
SCREEN_SETTINGS = "settings"
SCREEN_DETAILS = "details"
SCREEN_CLOCK = "clock"
SCREEN_TRACKED = "tracked"

SECONDARY_TIMEOUT_S = 45
BOOT_SPLASH_S = 3


class RoundTouchDisplay:
    def __init__(self):
        try:
            from config import DISPLAY_FULLSCREEN
            fullscreen = DISPLAY_FULLSCREEN
        except ImportError:
            fullscreen = os.environ.get("DISPLAY_FULLSCREEN", "true").lower() in ("1", "true", "yes")

        self.surface = video.init_display(theme.DISPLAY_WIDTH, theme.DISPLAY_HEIGHT, fullscreen)
        pygame.mouse.set_visible(False)
        pygame.event.set_allowed(
            None
        )  # allow all; we filter QUIT manually

        scale.select(settings.scale_index())
        settings.apply_theme_colors()

        self.overhead = Overhead()
        self.overhead.grab_data()

        self.input = input_handler.TouchInput()
        self.screen = SCREEN_RADAR
        self.settings_page = info.PAGE_MAIN
        self.flights = []
        self.flight_index = 0
        self._secondary_activity = time.time()
        self._boot_until = time.time() + BOOT_SPLASH_S
        self._last_clock_minute = -1
        self._last_radar_draw = 0
        self._last_static_draw = 0
        self._display_focus = 0
        self._fatal_error = None
        self._scroll = nav.ScrollState()
        self._last_grab_seq = 0

        radar._init_sweep()
        map_bg.request_background()
        map_bg.prewarm_all_scales()
        self._safe_draw()

    def _refresh_flights(self):
        try:
            if self.overhead.processing:
                return
            self.flights = self.overhead.peek_data()
        except Exception:
            logger.exception("Failed to refresh flight data")

    def _ordered_flights(self):
        return radar.flights_by_distance(self.flights)

    def _draw(self):
        if self._fatal_error:
            draw.draw_error(self.surface, self._fatal_error)
            draw.apply_round_bezel(self.surface)
            pygame.display.flip()
            return

        if time.time() < self._boot_until:
            details.draw_details(self.surface, boot_splash=True)
            draw.apply_round_bezel(self.surface)
            pygame.display.flip()
            return

        if self.screen == SCREEN_RADAR:
            radar.draw_radar(self.surface, self.flights)
        elif self.screen == SCREEN_FLIGHT:
            self._scroll.max_offset = flight_detail.draw_flight_detail(
                self.surface, self._ordered_flights(), self.flight_index, self._scroll.offset
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
        pygame.display.flip()

    def _timeout_remaining_fraction(self) -> float | None:
        """Fraction of secondary-screen timeout remaining, or None if not applicable."""
        if time.time() < self._boot_until:
            return None
        if self.screen in (SCREEN_RADAR, SCREEN_CLOCK):
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
                pygame.display.flip()
            except Exception:
                logger.exception("Could not render error screen")

    def _note_activity(self):
        self._secondary_activity = time.time()

    def _return_to_radar(self):
        self._fatal_error = None
        if self.screen == SCREEN_TRACKED:
            tracked.reset_marquee()
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
        if screen != self.screen:
            if self.screen == SCREEN_TRACKED:
                tracked.reset_marquee()
            self._scroll.reset()
        self.screen = screen

    def _apply_display_row(self, row: int):
        self._display_focus = row
        if row == 0:
            pct = settings.brightness_percent() + 5
            if pct > 100:
                pct = 10
            settings.set_brightness_percent(pct)
        elif row == 1:
            settings.toggle_distance_units()
        elif row == 2:
            settings.toggle_compass_rose()
        elif row == 3:
            settings.cycle_min_height()

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

        if swipe != input_handler.SWIPE_NONE and self.screen not in (SCREEN_RADAR, SCREEN_CLOCK):
            self._note_activity()

        # Tracked sits left of radar: swipe right on radar opens it; swipe left returns.
        if swipe == input_handler.SWIPE_RIGHT and self.screen == SCREEN_RADAR:
            opened = False
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
            else:
                self._open_screen(SCREEN_TRACKED)
                self._scroll.reset()
                self._note_activity()
                self._safe_draw()
        elif swipe == input_handler.SWIPE_LEFT and self.screen == SCREEN_TRACKED:
            self._return_to_radar()
            self._safe_draw()
        elif swipe == input_handler.SWIPE_DOWN and self.screen == SCREEN_RADAR:
            self._open_screen(SCREEN_CLOCK)
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
            elif self.screen == SCREEN_SETTINGS and self.settings_page == info.PAGE_COLORS:
                self._set_settings_page(info.PAGE_DISPLAY)
            elif self.screen == SCREEN_SETTINGS and self.settings_page == info.PAGE_DISPLAY:
                self._set_settings_page(info.PAGE_MAIN)
            else:
                self._return_to_radar()
            self._note_activity()
            self._safe_draw()
        elif tap and self.screen == SCREEN_RADAR:
            if self._open_flight_at(tap[0], tap[1]):
                self._safe_draw()
            elif radar.tap_on_range_header(tap[0], tap[1]):
                scale.cycle_next()
                settings.set_scale_index(scale.active_index())
                map_bg.request_background()
                self._safe_draw()
        elif tap and self.screen == SCREEN_FLIGHT:
            ordered = self._ordered_flights()
            action = flight_detail.tap_footer_action(tap[0], tap[1], ordered)
            if action == "prev" and ordered:
                self.flight_index = (self.flight_index - 1) % len(ordered)
                self._scroll.reset()
                self._note_activity()
                self._safe_draw()
            elif action == "next" and ordered:
                self.flight_index = (self.flight_index + 1) % len(ordered)
                self._scroll.reset()
                self._note_activity()
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
        elif tap and self.screen == SCREEN_CLOCK:
            action = clock.tap_footer_action(tap[0], tap[1])
            if action == "radar":
                self._return_to_radar()
                self._safe_draw()
            elif clock.tap_on_time(tap[0], tap[1]):
                settings.toggle_clock_format()
                self._note_activity()
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
        if self.screen in (SCREEN_RADAR, SCREEN_CLOCK):
            return
        if self.screen == SCREEN_TRACKED and tracked.is_pinned():
            return
        if time.time() - self._secondary_activity >= SECONDARY_TIMEOUT_S:
            self._return_to_radar()
            self._safe_draw()

    def _tick_clock(self):
        if self.screen != SCREEN_CLOCK:
            return
        minute = time.localtime().tm_min + time.localtime().tm_hour * 60
        if minute != self._last_clock_minute:
            self._last_clock_minute = minute
            self._safe_draw()

    def _maybe_reload_location(self):
        try:
            from config import reload_location_override
            from display.round_touch import map_bg

            if reload_location_override():
                map_bg.invalidate()
                map_bg.prewarm_all_scales()
                self._safe_draw()
        except ImportError:
            pass

    def _tick_data(self):
        try:
            self._refresh_flights()
            if not self.overhead.processing:
                self.overhead.grab_data()
        except Exception:
            logger.exception("Flight data poll failed")

    def run(self):
        logger.info("Round touch display starting (%dx%d)", theme.DISPLAY_WIDTH, theme.DISPLAY_HEIGHT)
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
                    if event.type in (
                        pygame.FINGERDOWN, pygame.FINGERUP,
                        pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP,
                        pygame.MOUSEMOTION,
                    ):
                        self.input.handle_event(event)
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
                elif self.screen == SCREEN_CLOCK:
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
                time.sleep(0.01)

        except KeyboardInterrupt:
            logger.info("Display stopped by user")
        except Exception:
            logger.exception("Display loop crashed")
            raise
        finally:
            pygame.quit()
