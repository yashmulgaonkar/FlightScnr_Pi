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
    position_smooth,
    rainviewer_overlay,
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
    wifi_setup as wifi_setup_screen,
)
from utilities import wifi_setup as wifi_setup_util

logger = logging.getLogger("flightscnr.display")

SCREEN_RADAR = "radar"
SCREEN_FLIGHT = "flight_detail"
SCREEN_SETTINGS = "settings"
SCREEN_DETAILS = "details"
SCREEN_CLOCK = "clock"
SCREEN_CLOCK_SETTINGS = "clock_settings"
SCREEN_FORECAST = "forecast"
SCREEN_TRACKED = "tracked"
SCREEN_WIFI_SETUP = "wifi_setup"

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
        try:
            from utilities.ais_client import sync_ais_client

            sync_ais_client()
        except Exception:
            logger.debug("AIS client startup sync skipped", exc_info=True)

        self.input = input_handler.TouchInput()
        self.pinch = pinch_handler.PinchZoom()
        self.gestures = gesture_handler.RadarGestureHandler(self.input, self.pinch)
        self._ghost_filter = ghost_touch_filter.GhostTouchFilter()
        self.screen = SCREEN_RADAR
        self.settings_page = info.PAGE_MAIN
        self.flights = []
        self._ais_vessels: list = []
        self._position_smoother = position_smooth.PositionSmoother()
        self._last_ais_poll = 0.0
        self.flight_index = 0
        # Stable identity for the open detail page (index alone drifts as traffic changes).
        self._selected_flight_id: str | None = None
        self._secondary_activity = time.time()
        self._boot_until = time.time() + BOOT_SPLASH_S
        self._wifi_setup_mode = False
        self._last_wifi_setup_poll = 0.0
        self._wifi_setup_redraw = False
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
        self._aircraft_photos: dict[str, dict] = {}
        self._aircraft_photo_inflight: set[str] = set()
        self._aircraft_photo_miss: set[str] = set()
        self._aircraft_photo_redraw = False
        self._vessel_photos: dict[str, dict] = {}
        self._vessel_photo_inflight: set[str] = set()
        self._vessel_photo_miss: set[str] = set()
        self._vessel_photo_redraw = False
        self._last_settings_reload = 0.0
        self._off_hours_wake_until = 0.0
        # Tracks whether force-clock off-hours was already active last tick
        # (edge-detect so we don't fight deliberate navigation to radar).
        self._off_hours_force_clock_active = False
        self._calibrating_facing = False
        self._facing_before_calibrate = 0.0
        self._facing_drag_angle = None
        self._panning_map = False
        self._pan_offset = (0, 0)
        self._pan_drag_start = None
        self._rgb_slider_channel: int | None = None
        self._brightness_slider_active = False
        self._vfr_opacity_slider_active = False

        radar._init_sweep()
        try:
            self._wifi_setup_mode = bool(wifi_setup_util.should_enter_setup_at_boot())
        except Exception:
            logger.exception("Wi-Fi setup probe failed")
            self._wifi_setup_mode = False
        if self._wifi_setup_mode:
            self.screen = SCREEN_WIFI_SETUP
            logger.info("Entering Wi-Fi setup hotspot mode")
            try:
                wifi_setup_util.clear_wifi_connected_flag()
            except Exception:
                pass
            Thread(
                target=self._ensure_wifi_setup_ap,
                name="wifi-setup-ap",
                daemon=True,
            ).start()
        else:
            map_bg.request_background()
            map_bg.prewarm_all_scales()
            rainviewer_overlay.request_overlay()
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

    def _ensure_wifi_setup_ap(self) -> None:
        """Start the setup hotspot off the UI thread (never call pygame here)."""
        try:
            wifi_setup_util.ensure_setup_ap()
        except Exception:
            logger.exception("Failed to start Wi-Fi setup hotspot")
        # Main loop picks this up — pygame is not thread-safe on this display.
        self._wifi_setup_redraw = True

    def _leave_wifi_setup(self) -> None:
        """Exit QR/setup screen after home Wi-Fi is up."""
        if self.screen != SCREEN_WIFI_SETUP and not self._wifi_setup_mode:
            return
        logger.info("Wi-Fi client connected — leaving setup mode")
        try:
            wifi_setup_util.stop_setup_ap()
        except Exception:
            logger.debug("Setup AP stop after connect", exc_info=True)
        try:
            wifi_setup_util.clear_wifi_connected_flag()
        except Exception:
            pass
        self._wifi_setup_mode = False
        self._fatal_error = None
        map_bg.request_background()
        map_bg.prewarm_all_scales()
        rainviewer_overlay.request_overlay()
        self._open_screen(SCREEN_RADAR)

    def _tick_wifi_setup(self) -> None:
        if self.screen != SCREEN_WIFI_SETUP:
            return
        if self._wifi_setup_redraw:
            self._wifi_setup_redraw = False
            self._safe_draw()
        now = time.time()
        if now - self._last_wifi_setup_poll < 1.0:
            return
        self._last_wifi_setup_poll = now
        try:
            connected = (
                wifi_setup_util.wifi_connect_signaled()
                or wifi_setup_util.active_client_wifi()
            )
        except Exception:
            logger.debug("Wi-Fi setup poll failed", exc_info=True)
            return
        if connected:
            self._leave_wifi_setup()

    def _refresh_ais_vessels(self) -> None:
        """Re-read the local AIS vessel table (WebSocket feed is separate)."""
        if not settings.ais_enabled():
            self._ais_vessels = []
            return
        try:
            from utilities.ais_client import fetch_ais_radar_entries

            self._ais_vessels = fetch_ais_radar_entries() or []
        except Exception:
            logger.exception("[ais] failed to refresh vessel snapshot")

    def _refresh_flights(self):
        try:
            scale.select(settings.scale_index())
            if self.overhead.processing:
                return
            flights = list(self.overhead.peek_data() or [])
            mode = settings.traffic_mode()
            if mode == "marine":
                flights = []
            if mode in ("marine", "both") and self._ais_vessels:
                flights.extend(self._ais_vessels)
            self.flights = flights
            if self.screen == SCREEN_FLIGHT:
                self._sync_selected_flight_index()
        except Exception:
            logger.exception("Failed to refresh flight data")

    @staticmethod
    def _flight_identity(flight: dict | None) -> str | None:
        """Stable key for a flight/vessel across radar refresh / distance re-sorts."""
        if not flight:
            return None
        if flight.get("kind") == "vessel":
            mmsi = str(flight.get("mmsi") or "").strip()
            if mmsi:
                return f"mmsi:{mmsi}"
        hex_id = (flight.get("icao_hex") or "").strip().upper()
        if hex_id:
            return f"hex:{hex_id}"
        callsign = (
            flight.get("callsign")
            or flight.get("flight_number")
            or flight.get("name")
            or ""
        ).strip().upper()
        if callsign:
            return f"cs:{callsign}"
        return None

    def _ordered_flights(self):
        return radar.flights_by_distance(self.flights)

    def _sync_selected_flight_index(self) -> bool:
        """Keep flight_index pointing at `_selected_flight_id` after list changes.

        Returns True if the selected flight is still present.
        """
        ordered = self._ordered_flights()
        if not ordered:
            self.flight_index = 0
            return False
        selected_id = self._selected_flight_id
        if selected_id:
            for i, flight in enumerate(ordered):
                if self._flight_identity(flight) == selected_id:
                    self.flight_index = i
                    return True
        # Selected aircraft left coverage — keep a valid index, but clear the pin
        # so we don't keep showing whoever now occupies the old slot forever.
        self.flight_index = max(0, min(self.flight_index, len(ordered) - 1))
        self._selected_flight_id = self._flight_identity(ordered[self.flight_index])
        return False

    def _select_flight_at_index(self, index: int, ordered: list | None = None) -> None:
        ordered = ordered if ordered is not None else self._ordered_flights()
        if not ordered:
            self.flight_index = 0
            self._selected_flight_id = None
            return
        self.flight_index = index % len(ordered)
        self._selected_flight_id = self._flight_identity(ordered[self.flight_index])

    def _select_flight(self, flight: dict, ordered: list | None = None) -> None:
        ordered = ordered if ordered is not None else self._ordered_flights()
        if not ordered:
            self.flight_index = 0
            self._selected_flight_id = self._flight_identity(flight)
            return
        selected_id = self._flight_identity(flight)
        self._selected_flight_id = selected_id
        if selected_id:
            for i, candidate in enumerate(ordered):
                if self._flight_identity(candidate) == selected_id:
                    self.flight_index = i
                    return
        try:
            self.flight_index = ordered.index(flight)
        except ValueError:
            self.flight_index = 0
            self._selected_flight_id = self._flight_identity(ordered[0])

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

        if self.screen == SCREEN_WIFI_SETUP:
            wifi_setup_screen.draw_wifi_setup(self.surface)
        elif self.screen == SCREEN_RADAR:
            radar.draw_radar(
                self.surface,
                self._radar_flights(),
                calibrate=self._calibrating_facing,
                pan_mode=self._panning_map,
                pan_offset=self._pan_offset if self._panning_map else None,
            )
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

    def _timeout_duration_s(self) -> float | None:
        """Active secondary-screen timeout in seconds, or None if no countdown."""
        if time.time() < self._boot_until:
            return None
        if self.screen == SCREEN_WIFI_SETUP:
            return None
        if self.screen in (SCREEN_RADAR, SCREEN_CLOCK, SCREEN_CLOCK_SETTINGS, SCREEN_FORECAST):
            return None
        if self.screen == SCREEN_TRACKED and tracked.is_pinned():
            return None
        if self.screen == SCREEN_FLIGHT:
            return float(settings.flight_detail_timeout_s())
        return float(SECONDARY_TIMEOUT_S)

    def _timeout_remaining_fraction(self) -> float | None:
        """Fraction of secondary-screen timeout remaining, or None if not applicable."""
        timeout_s = self._timeout_duration_s()
        if timeout_s is None:
            return None
        if timeout_s <= 0:
            return None
        elapsed = time.time() - self._secondary_activity
        return max(0.0, (timeout_s - elapsed) / timeout_s)

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

    def _radar_modal_active(self) -> bool:
        """Facing calibrate or map pan — swallow navigation and pause traffic."""
        return self._calibrating_facing or self._panning_map

    def _return_to_radar(self):
        self._fatal_error = None
        if self._calibrating_facing:
            self._cancel_facing_calibrate()
        if self._panning_map:
            self._cancel_map_pan()
        previous = self.screen
        if self.screen == SCREEN_TRACKED:
            tracked.reset_marquee()
        self._radar_visible_since = time.time()
        self._auto_idle_clock = False
        self.screen = SCREEN_RADAR
        self.settings_page = info.PAGE_MAIN
        self._selected_flight_id = None
        self._scroll.reset()
        self._maybe_reflash_alerts(previous)

    def _set_settings_page(self, page: int):
        if self._rgb_slider_channel is not None:
            settings.persist_theme_settings()
            self._rgb_slider_channel = None
        self._brightness_slider_active = False
        self._vfr_opacity_slider_active = False
        if page != self.settings_page:
            self._scroll.reset()
            if page not in (info.PAGE_DISPLAY, info.PAGE_OPTIONS):
                self._display_focus = 0
        self.settings_page = page

    def _maybe_reflash_alerts(self, previous_screen: str | None) -> None:
        """Short rim pulse when coming back to radar with an alert still visible."""
        if previous_screen == SCREEN_RADAR:
            return
        try:
            flights = self._radar_flights() if hasattr(self, "_radar_flights") else self.flights
            aircraft_alert.reflash_for_visible_alerts(flights or self.flights)
        except Exception:
            logger.debug("Alert reflash on radar entry failed", exc_info=True)

    def _open_screen(self, screen: str):
        if screen == SCREEN_CLOCK:
            self._last_clock_minute = -1
            self._last_clock_draw = 0.0
        previous = self.screen
        if screen != self.screen:
            if self.screen == SCREEN_TRACKED:
                tracked.reset_marquee()
            self._scroll.reset()
        if screen == SCREEN_RADAR:
            self._radar_visible_since = time.time()
            self._auto_idle_clock = False
            self.screen = screen
            self._maybe_reflash_alerts(previous)
            return
        # Reset secondary timeout window when entering any non-radar screen.
        # Without this, a stale timestamp can immediately bounce back to radar.
        self._note_activity()
        self.screen = screen
        if screen == SCREEN_CLOCK:
            self._safe_draw()

    def _apply_display_row(self, page: int, row: int):
        action = info.display_action_at(page, row)
        if action is None:
            return
        self._display_focus = row
        if action == "traffic":
            settings.cycle_traffic_mode()
            self._last_ais_poll = 0.0
            self._tick_ais()
            self._refresh_flights()
        elif action == "brightness":
            # Brightness is a drag slider; taps are handled via brightness_slider_at.
            return
        elif action == "units":
            settings.toggle_distance_units()
        elif action == "range":
            settings.cycle_scale()
            scale.select(settings.scale_index())
            map_bg.request_background()
            rainviewer_overlay.request_overlay()
        elif action == "rotate":
            settings.cycle_display_rotation()
        elif action == "compass":
            settings.toggle_compass_rose()
        elif action == "range_rings":
            settings.toggle_range_rings()
        elif action == "facing":
            self._begin_facing_calibrate()
        elif action == "recenter":
            self._begin_map_pan()
        elif action == "aircraft_tag":
            settings.toggle_show_aircraft_tag()
        elif action == "min_height":
            settings.cycle_min_height()
        elif action == "max_height":
            settings.cycle_max_height()
        elif action == "sweep":
            settings.toggle_sweep_line()
        elif action == "precipitation":
            settings.toggle_show_precipitation()
            rainviewer_overlay.invalidate()
            rainviewer_overlay.request_overlay()
        elif action == "map_style":
            settings.cycle_map_style()
        elif action == "vfr_opacity":
            # VFR opacity is a drag slider; taps are handled via vfr_opacity_slider_at.
            return
        elif action == "idle_clock":
            settings.toggle_auto_idle_clock()

    def _begin_facing_calibrate(self):
        """Enter radar facing-calibrate mode (circular drag = dial analogue)."""
        if self._panning_map:
            self._cancel_map_pan()
        self._facing_before_calibrate = settings.facing_deg()
        settings.set_facing_preview(self._facing_before_calibrate)
        self._calibrating_facing = True
        self._facing_drag_angle = None
        self.flights = []
        self._open_screen(SCREEN_RADAR)

    def _cancel_facing_calibrate(self):
        if not self._calibrating_facing:
            return
        settings.set_facing_preview(None)
        self._calibrating_facing = False
        self._facing_drag_angle = None
        self._refresh_flights()

    def _save_facing_calibrate(self):
        if not self._calibrating_facing:
            return
        preview = settings.effective_facing_deg()
        settings.set_facing_deg(preview)
        settings.set_facing_preview(None)
        self._calibrating_facing = False
        self._facing_drag_angle = None
        self._refresh_flights()

    def _begin_map_pan(self):
        """Enter map-pan mode: drag map, tap center to set new radar home."""
        if self._calibrating_facing:
            self._cancel_facing_calibrate()
        self._panning_map = True
        self._pan_offset = (0, 0)
        self._pan_drag_start = None
        self.flights = []
        self._open_screen(SCREEN_RADAR)

    def _cancel_map_pan(self):
        if not self._panning_map:
            return
        self._panning_map = False
        self._pan_offset = (0, 0)
        self._pan_drag_start = None
        self._refresh_flights()

    def _save_map_pan(self):
        """Persist the geographic point under the crosshair as LOCATION_HOME."""
        if not self._panning_map:
            return
        from config import set_location_home
        from display.round_touch import geo, weather_data

        ox, oy = self._pan_offset
        lat, lon = geo.screen_to_lat_lon(
            theme.CENTER_X - ox,
            theme.CENTER_Y - oy,
        )
        set_location_home(lat, lon)
        map_bg.invalidate()
        map_bg.prewarm_all_scales()
        rainviewer_overlay.invalidate()
        rainviewer_overlay.request_overlay()
        self._position_smoother.reset()
        self._panning_map = False
        self._pan_offset = (0, 0)
        self._pan_drag_start = None

        def _after_recenter():
            try:
                weather_data.after_radar_center_changed(lat, lon)
            except Exception:
                logger.exception("Weather/timezone refresh after map recenter failed")
            else:
                self._weather_redraw_pending = True

        Thread(target=_after_recenter, daemon=True).start()
        self.overhead.grab_data()
        self._refresh_flights()

    def _update_map_pan_drag(self) -> bool:
        """Translate finger motion into a live map pixel offset."""
        if not self._panning_map or self.screen != SCREEN_RADAR:
            self._pan_drag_start = None
            return False
        if not self.input.is_dragging():
            self._pan_drag_start = None
            return False
        pos = self.input.drag_pos()
        if pos is None:
            return False
        if self._pan_drag_start is None:
            self._pan_drag_start = (
                pos[0],
                pos[1],
                self._pan_offset[0],
                self._pan_offset[1],
            )
            return False
        sx, sy, ox0, oy0 = self._pan_drag_start
        self._pan_offset = (ox0 + (pos[0] - sx), oy0 + (pos[1] - sy))
        return True

    @staticmethod
    def _angle_about_center(x: float, y: float) -> float:
        """Screen angle in degrees: 0 = up, clockwise positive."""
        return math.degrees(math.atan2(x - theme.CENTER_X, theme.CENTER_Y - y))

    def _update_facing_drag(self):
        """Apply circular-drag delta to the live facing preview."""
        if not self._calibrating_facing or self.screen != SCREEN_RADAR:
            self._facing_drag_angle = None
            return False
        if not self.input.is_dragging():
            self._facing_drag_angle = None
            return False
        pos = self.input.drag_pos()
        if pos is None:
            return False
        x, y = pos
        # Ignore near-center jitter — angle is unstable there.
        if math.hypot(x - theme.CENTER_X, y - theme.CENTER_Y) < theme.s(40):
            return False
        angle = self._angle_about_center(x, y)
        if self._facing_drag_angle is None:
            self._facing_drag_angle = angle
            return False
        delta = angle - self._facing_drag_angle
        # Unwrap across ±180 so continuous circles work.
        if delta > 180:
            delta -= 360
        elif delta < -180:
            delta += 360
        self._facing_drag_angle = angle
        # Clockwise finger motion decreases facing (rose turns with the finger).
        preview = (settings.effective_facing_deg() - delta) % 360.0
        settings.set_facing_preview(preview)
        return True

    def _facing_tap_action(self, x: int, y: int) -> str | None:
        """Return 'save' (center), 'cancel' (outer rim), or None."""
        dist = math.hypot(x - theme.CENTER_X, y - theme.CENTER_Y)
        if dist <= theme.s(70):
            return "save"
        if dist >= theme.VISIBLE_RADIUS - theme.s(48):
            return "cancel"
        return None

    def _map_pan_tap_action(self, x: int, y: int) -> str | None:
        """Same center/rim targets as facing calibrate."""
        return self._facing_tap_action(x, y)

    def _apply_brightness(self):
        from display.round_touch import backlight, off_hours

        day_pct = settings.brightness_percent()
        pct = off_hours.effective_brightness_percent(day_pct)
        # Display-off mode: temporary wake after touch keeps daytime brightness.
        if pct == 0 and time.time() < self._off_hours_wake_until:
            pct = day_pct
        # Legacy off-hours "clock" mode is always full daytime brightness (even
        # on the clock screen itself). While on radar (or other non-clock
        # screens) in that mode, restore daytime brightness so traffic is
        # readable. Dim mode already has its own configured dim_percent that
        # should apply uniformly across all screens, radar included.
        elif (
            off_hours.in_off_hours()
            and off_hours.prefs().get("mode") == "clock"
            and self.screen
            not in (SCREEN_CLOCK, SCREEN_CLOCK_SETTINGS, SCREEN_FORECAST)
        ):
            pct = day_pct
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
        rainviewer_overlay.request_overlay()
        self._safe_draw()

    def _flights_for_detail(self):
        self._sync_selected_flight_index()
        ordered = self._ordered_flights()
        out = []
        for f in ordered:
            merged = merge_route_enrichment(f, self._route_enrichment)
            if merged.get("kind") == "vessel":
                merged = self._merge_vessel_photo(merged)
            else:
                merged = self._merge_aircraft_photo(merged)
            out.append(merged)
        return out

    def _merge_aircraft_photo(self, flight: dict) -> dict:
        from utilities.aircraft_photo import normalize_icao_hex, photo_credit_line

        hex_id = normalize_icao_hex(flight.get("icao_hex") or flight.get("hex"))
        if not hex_id:
            return flight
        photo = self._aircraft_photos.get(hex_id)
        if not photo:
            return flight
        merged = dict(flight)
        merged["photo_path"] = photo.get("path") or ""
        merged["photo_credit"] = photo_credit_line(photo)
        return merged

    def _merge_vessel_photo(self, vessel: dict) -> dict:
        from utilities.vessel_photo import vessel_photo_cache_key

        key = vessel_photo_cache_key(vessel)
        photo = self._vessel_photos.get(key)
        if not photo:
            return vessel
        merged = dict(vessel)
        merged["photo_path"] = photo.get("path") or ""
        artist = (photo.get("artist") or "").strip()
        license_name = (photo.get("license") or "").strip()
        bits = [b for b in (artist, license_name, "Wikimedia Commons") if b]
        # Keep credit short for the round display
        credit = " · ".join(bits[:2]) if bits else "Wikimedia Commons"
        if len(credit) > 42:
            credit = credit[:39] + "…"
        merged["photo_credit"] = credit
        return merged

    def _maybe_enrich_flight_detail(self):
        """Fetch route / photo enrichment for the open detail row."""
        if self.screen != SCREEN_FLIGHT:
            return
        self._sync_selected_flight_index()
        ordered = self._ordered_flights()
        if not ordered:
            return
        idx = max(0, min(self.flight_index, len(ordered) - 1))
        flight = ordered[idx]
        if flight.get("kind") == "vessel":
            self._maybe_fetch_vessel_photo(flight)
            return
        self._maybe_fetch_aircraft_photo(flight)
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

    def _maybe_fetch_aircraft_photo(self, flight: dict) -> None:
        from utilities.aircraft_photo import (
            fetch_aircraft_photo_for,
            get_cached_aircraft_photo,
            normalize_icao_hex,
        )

        hex_id = normalize_icao_hex(flight.get("icao_hex") or flight.get("hex"))
        if not hex_id:
            return
        if hex_id in self._aircraft_photos or hex_id in self._aircraft_photo_miss:
            return
        if hex_id in self._aircraft_photo_inflight:
            return

        cached = get_cached_aircraft_photo(hex_id)
        if cached:
            self._aircraft_photos[hex_id] = cached
            self._aircraft_photo_redraw = True
            return

        self._aircraft_photo_inflight.add(hex_id)
        snapshot = dict(flight)

        def _work():
            try:
                photo = fetch_aircraft_photo_for(snapshot)
                if photo and photo.get("path"):
                    self._aircraft_photos[hex_id] = photo
                    self._aircraft_photo_redraw = True
                    logger.info("[photo] detail ready for %s", hex_id)
                else:
                    self._aircraft_photo_miss.add(hex_id)
            finally:
                self._aircraft_photo_inflight.discard(hex_id)

        Thread(target=_work, daemon=True).start()

    def _maybe_fetch_vessel_photo(self, vessel: dict) -> None:
        from utilities.vessel_photo import (
            fetch_vessel_photo_for,
            get_cached_vessel_photo,
            vessel_photo_cache_key,
        )

        key = vessel_photo_cache_key(vessel)
        if not key or key in self._vessel_photos or key in self._vessel_photo_miss:
            return
        if key in self._vessel_photo_inflight:
            return

        cached = get_cached_vessel_photo(
            vessel.get("name") or vessel.get("callsign") or "",
            vessel.get("imo") or "",
            vessel.get("mmsi") or "",
        )
        if cached:
            self._vessel_photos[key] = cached
            self._vessel_photo_redraw = True
            return

        self._vessel_photo_inflight.add(key)
        snapshot = dict(vessel)

        def _work():
            try:
                photo = fetch_vessel_photo_for(snapshot)
                if photo and photo.get("path"):
                    self._vessel_photos[key] = photo
                    self._vessel_photo_redraw = True
                    logger.info(
                        "[commons] detail photo ready for %r",
                        snapshot.get("name") or snapshot.get("mmsi"),
                    )
                else:
                    self._vessel_photo_miss.add(key)
            finally:
                self._vessel_photo_inflight.discard(key)

        Thread(target=_work, daemon=True).start()

    def _radar_flights(self) -> list:
        """Flights with dead-reckoned positions for radar draw / tap hit-testing."""
        return self._position_smoother.apply(self.flights)

    def _open_flight_at(self, x: int, y: int, alt_x: int | None = None, alt_y: int | None = None) -> bool:
        picked = radar.pick_flight_at(self._radar_flights(), x, y, alt_x, alt_y)
        ordered = self._ordered_flights()
        if not picked or not ordered:
            return False
        self._select_flight(picked, ordered)
        if picked.get("kind") == "vessel":
            logger.info(
                "[ais] selected vessel MMSI=%s name=%r",
                picked.get("mmsi"),
                picked.get("name") or picked.get("callsign"),
            )
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
        if self.screen == SCREEN_SETTINGS and self.settings_page == info.PAGE_COLORS:
            if self._rgb_slider_channel is not None:
                self.input.consume_scroll_drag()
                return
            if self.input.is_dragging():
                pos = self.input.drag_pos()
                if pos and info.theme_slider_at(pos[0], pos[1], self._scroll.offset) is not None:
                    self.input.consume_scroll_drag()
                    return
        if self.screen == SCREEN_SETTINGS and self.settings_page == info.PAGE_DISPLAY:
            if self._brightness_slider_active:
                self.input.consume_scroll_drag()
                return
            if self.input.is_dragging():
                pos = self.input.drag_pos()
                if pos and info.brightness_slider_at(pos[0], pos[1], self._scroll.offset):
                    self.input.consume_scroll_drag()
                    return
        if self.screen == SCREEN_SETTINGS and self.settings_page == info.PAGE_OPTIONS:
            if self._vfr_opacity_slider_active:
                self.input.consume_scroll_drag()
                return
            if self.input.is_dragging():
                pos = self.input.drag_pos()
                if pos and info.vfr_opacity_slider_at(pos[0], pos[1], self._scroll.offset):
                    self.input.consume_scroll_drag()
                    return
        dy = self.input.consume_scroll_drag()
        if not dy:
            return
        if self.screen == SCREEN_FLIGHT:
            self._apply_scroll_delta(-dy)
        elif self.screen == SCREEN_DETAILS:
            self._apply_scroll_delta(-dy)
        elif self.screen == SCREEN_SETTINGS:
            self._apply_scroll_delta(-dy)

    def _apply_theme_slider(self, channel: int, x: int, *, persist: bool) -> bool:
        value = info.theme_slider_value_at(x, channel, self._scroll.offset)
        if value is None:
            return False
        rgb = list(settings.theme_rgb())
        if rgb[channel] == value and settings.theme_custom():
            return False
        rgb[channel] = value
        settings.set_custom_theme_rgb(*rgb, persist=persist)
        return True

    def _apply_brightness_slider(self, x: int, *, persist: bool = True) -> bool:
        value = info.brightness_slider_value_at(x, self._scroll.offset)
        if value is None:
            return False
        if value == settings.brightness_percent():
            self._display_focus = info.brightness_row_index()
            return False
        settings.set_brightness_percent(value, persist=persist)
        self._display_focus = info.brightness_row_index()
        self._apply_brightness()
        return True

    def _update_theme_rgb_drag(self) -> bool:
        """Horizontal drag on Theme RGB sliders; suppresses page scroll while active."""
        if self.screen != SCREEN_SETTINGS or self.settings_page != info.PAGE_COLORS:
            if self._rgb_slider_channel is not None:
                settings.persist_theme_settings()
                self._rgb_slider_channel = None
            return False
        if not self.input.is_dragging():
            if self._rgb_slider_channel is not None:
                settings.persist_theme_settings()
                self._rgb_slider_channel = None
                self.input.consume_scroll_drag()
                return True
            return False
        pos = self.input.drag_pos()
        if pos is None:
            return False
        x, y = pos
        if self._rgb_slider_channel is None:
            channel = info.theme_slider_at(x, y, self._scroll.offset)
            if channel is None:
                return False
            self._rgb_slider_channel = channel
        changed = self._apply_theme_slider(self._rgb_slider_channel, x, persist=False)
        self.input.consume_scroll_drag()
        return changed

    def _update_brightness_slider_drag(self) -> bool:
        """Horizontal drag on Display brightness slider; suppresses page scroll while active."""
        if self.screen != SCREEN_SETTINGS or self.settings_page != info.PAGE_DISPLAY:
            self._brightness_slider_active = False
            return False
        if not self.input.is_dragging():
            if self._brightness_slider_active:
                self._brightness_slider_active = False
                settings.set_brightness_percent(settings.brightness_percent(), persist=True)
                self.input.consume_scroll_drag()
                return True
            return False
        pos = self.input.drag_pos()
        if pos is None:
            return False
        x, y = pos
        if not self._brightness_slider_active:
            if not info.brightness_slider_at(x, y, self._scroll.offset):
                return False
            self._brightness_slider_active = True
        changed = self._apply_brightness_slider(x, persist=False)
        self.input.consume_scroll_drag()
        return changed

    def _apply_vfr_opacity_slider(self, x: int, *, persist: bool = True) -> bool:
        value = info.vfr_opacity_slider_value_at(x, self._scroll.offset)
        if value is None:
            return False
        if value == settings.vfr_map_opacity():
            self._display_focus = info.vfr_opacity_row_index()
            return False
        settings.set_vfr_map_opacity(value, persist=persist)
        self._display_focus = info.vfr_opacity_row_index()
        return True

    def _update_vfr_opacity_slider_drag(self) -> bool:
        """Horizontal drag on Options VFR opacity slider; suppresses page scroll while active."""
        if self.screen != SCREEN_SETTINGS or self.settings_page != info.PAGE_OPTIONS:
            self._vfr_opacity_slider_active = False
            return False
        if not self.input.is_dragging():
            if self._vfr_opacity_slider_active:
                self._vfr_opacity_slider_active = False
                settings.set_vfr_map_opacity(settings.vfr_map_opacity(), persist=True)
                self.input.consume_scroll_drag()
                return True
            return False
        pos = self.input.drag_pos()
        if pos is None:
            return False
        x, y = pos
        if not self._vfr_opacity_slider_active:
            if not info.vfr_opacity_slider_at(x, y, self._scroll.offset):
                return False
            self._vfr_opacity_slider_active = True
        changed = self._apply_vfr_opacity_slider(x, persist=False)
        self.input.consume_scroll_drag()
        return changed

    def _handle_settings_tap(self, x: int | None = None, y: int | None = None):
        if (
            self.settings_page in (info.PAGE_DISPLAY, info.PAGE_OPTIONS)
            and x is not None
            and y is not None
        ):
            if self.settings_page == info.PAGE_DISPLAY and info.brightness_slider_at(
                x, y, self._scroll.offset
            ):
                self._apply_brightness_slider(x, persist=True)
                return
            if self.settings_page == info.PAGE_OPTIONS and info.vfr_opacity_slider_at(
                x, y, self._scroll.offset
            ):
                self._apply_vfr_opacity_slider(x, persist=True)
                return
            row = info.display_row_at(x, y, self.settings_page, self._scroll.offset)
            if row is not None:
                self._apply_display_row(self.settings_page, row)
        elif self.settings_page == info.PAGE_COLORS and x is not None and y is not None:
            row = info.theme_row_at(x, y, self._scroll.offset)
            if row is not None:
                settings.set_theme_index(row)
                return
            channel = info.theme_slider_at(x, y, self._scroll.offset)
            if channel is not None:
                self._apply_theme_slider(channel, x, persist=True)

    def _handle_navigation(self):
        if time.time() < self._boot_until:
            return
        if self.screen == SCREEN_WIFI_SETUP:
            # Captive portal owns input during first-time Wi-Fi setup.
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

        # Facing calibrate / map pan: center tap saves; rim tap cancels.
        # Swipes from drag gestures are discarded so they don't navigate or abort.
        if self._radar_modal_active() and self.screen == SCREEN_RADAR:
            if swipe != input_handler.SWIPE_NONE:
                return
            if tap:
                action = self._facing_tap_action(tap[0], tap[1])
                if action == "save":
                    if self._calibrating_facing:
                        self._save_facing_calibrate()
                    else:
                        self._save_map_pan()
                    self._note_activity()
                    self._safe_draw()
                elif action == "cancel":
                    if self._calibrating_facing:
                        self._cancel_facing_calibrate()
                    else:
                        self._cancel_map_pan()
                    self._note_activity()
                    self._safe_draw()
                return

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
        elif swipe in (input_handler.SWIPE_UP, input_handler.SWIPE_DOWN) and self.screen == SCREEN_SETTINGS:
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
                self._set_settings_page(info.PAGE_OPTIONS)
            elif self.screen == SCREEN_SETTINGS and self.settings_page == info.PAGE_OPTIONS:
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
            if tap and not self._radar_modal_active() and self._open_flight_at(tap[0], tap[1]):
                self._safe_draw()
        elif tap and self.screen == SCREEN_FLIGHT:
            self._sync_selected_flight_index()
            ordered = self._ordered_flights()
            action = flight_detail.tap_footer_action(tap[0], tap[1], ordered)
            if action == "prev" and ordered:
                self._select_flight_at_index(self.flight_index - 1, ordered)
                self._scroll.reset()
                self._note_activity()
                self._maybe_enrich_flight_detail()
                self._safe_draw()
            elif action == "next" and ordered:
                self._select_flight_at_index(self.flight_index + 1, ordered)
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
        if self.screen == SCREEN_WIFI_SETUP:
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

        timeout_s = self._timeout_duration_s()
        if timeout_s is None:
            # Clock/forecast use their own duration but share activity timestamp.
            if self.screen in (SCREEN_CLOCK, SCREEN_CLOCK_SETTINGS, SCREEN_FORECAST):
                timeout_s = float(settings.clock_timeout_s())
            else:
                return

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
        if self._radar_modal_active():
            return
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
        force_now = off_hours.in_off_hours() and off_hours.force_clock_enabled()
        was_force = self._off_hours_force_clock_active
        self._off_hours_force_clock_active = force_now
        if not force_now:
            return
        # Only snap to clock when force-clock off-hours *begins*. Running this
        # every frame made radar unreachable after a deliberate swipe to radar
        # (https://github.com/yashmulgaonkar/FlightScnr_Pi/issues/18).
        if was_force:
            return
        if self.screen not in (SCREEN_CLOCK, SCREEN_CLOCK_SETTINGS, SCREEN_FORECAST):
            self._open_screen(SCREEN_CLOCK)
            self._safe_draw()

    def _apply_reloaded_settings(self):
        """Apply settings written by another process (e.g. web portal)."""
        scale.select(settings.scale_index())
        map_bg.invalidate()
        map_bg.request_background()
        map_bg.prewarm_all_scales()
        rainviewer_overlay.request_overlay()
        self._apply_brightness()
        try:
            from utilities.ais_client import sync_ais_client

            sync_ais_client()
            # Force an AIS snapshot soon after enable/disable or range changes.
            self._last_ais_poll = 0.0
        except Exception:
            logger.debug("AIS sync after settings reload failed", exc_info=True)
        self._safe_draw()

    def _maybe_reload_location(self):
        try:
            from config import LOCATION_HOME, reload_location_override
            from display.round_touch import map_bg, weather_data

            if not reload_location_override():
                return
            map_bg.invalidate()
            map_bg.prewarm_all_scales()
            self._position_smoother.reset()
            self.overhead.grab_data()
            lat, lon = float(LOCATION_HOME[0]), float(LOCATION_HOME[1])

            def _after_recenter():
                try:
                    weather_data.after_radar_center_changed(lat, lon)
                except Exception:
                    logger.exception("Weather/timezone refresh after location change failed")
                else:
                    self._weather_redraw_pending = True

            Thread(target=_after_recenter, daemon=True).start()
            self._safe_draw()
        except ImportError:
            pass

    def _tick_data(self):
        if self._radar_modal_active():
            return
        try:
            scale.select(settings.scale_index())
            self._refresh_flights()
            if not self.overhead.processing:
                self.overhead.grab_data()
            if aircraft_alert.check_new_aircraft(self.flights):
                # Don't bury attention on Idle clock / forecast — jump back to radar.
                if self.screen in (
                    SCREEN_CLOCK,
                    SCREEN_CLOCK_SETTINGS,
                    SCREEN_FORECAST,
                ):
                    self._return_to_radar()
                    self._safe_draw()
        except Exception:
            logger.exception("Flight data poll failed")

    def _tick_ais(self):
        if self._radar_modal_active():
            return
        try:
            self._refresh_ais_vessels()
            self._refresh_flights()
        except Exception:
            logger.exception("[ais] vessel poll failed")

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
        pinch_diag_deadline = time.time() + 25.0
        pinch_diag_logged = False
        try:
            from config import AIS_REFRESH_SECONDS, DATA_REFRESH_SECONDS
        except ImportError:
            DATA_REFRESH_SECONDS = 2.0
            AIS_REFRESH_SECONDS = 5.0

        try:
            while running:
                if (
                    not pinch_diag_logged
                    and time.time() >= pinch_diag_deadline
                ):
                    pinch_diag_logged = True
                    if not input_handler.finger_events_seen():
                        logger.info(
                            "Pinch-to-zoom unavailable: no SDL FINGER* events yet "
                            "(common under Xwayland mouse emulation). "
                            "Taps/swipes still use the mouse path. "
                            "Change range via Settings → Options → Range, "
                            "or see README / GitHub issue #21."
                        )
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
                            ptr_down = (
                                not input_handler.use_finger_events()
                                and gesture_handler.RadarGestureHandler.is_pointer_down(event)
                            ) or (
                                input_handler.use_finger_events()
                                and event.type == pygame.FINGERDOWN
                            )
                            ptr_up = (
                                not input_handler.use_finger_events()
                                and gesture_handler.RadarGestureHandler.is_pointer_up(event)
                            ) or (
                                input_handler.use_finger_events()
                                and event.type == pygame.FINGERUP
                                and int(event.finger_id)
                                == self.gestures.touch.active_finger_id()
                            )
                            if ptr_down:
                                if input_handler.use_finger_events():
                                    # First finger only — later fingers are pinch partners.
                                    if self.gestures.pinch.finger_count() == 0:
                                        self.gestures.on_pointer_down()
                                else:
                                    self.gestures.on_pointer_down()
                            elif ptr_up:
                                self.gestures.on_pointer_up()
                            elif (
                                input_handler.use_finger_events()
                                and event.type == pygame.MOUSEBUTTONUP
                                and event.button == 1
                                and not self.gestures.touch.is_dragging()
                                and not self.gestures.pinch.is_pinching()
                            ):
                                self.gestures.on_pointer_up()
                        self.gestures.handle_input_event(event)
                        if (
                            self.screen == SCREEN_RADAR
                            and not self._radar_modal_active()
                            and gesture_handler.RadarGestureHandler.is_finger_event(event)
                        ):
                            scale_delta = self.gestures.handle_finger_event(event)
                            if scale_delta:
                                self._apply_scale_step(scale_delta)
                        self._handle_navigation()

                if (
                    self._update_facing_drag()
                    or self._update_map_pan_drag()
                    or self._update_theme_rgb_drag()
                    or self._update_brightness_slider_drag()
                    or self._update_vfr_opacity_slider_drag()
                ):
                    self._safe_draw()
                    self._last_radar_draw = time.time()

                now = time.time()
                if (
                    self.screen != SCREEN_WIFI_SETUP
                    and not self._radar_modal_active()
                    and now - last_data_poll >= DATA_REFRESH_SECONDS
                ):
                    self._tick_data()
                    last_data_poll = now

                if (
                    self.screen != SCREEN_WIFI_SETUP
                    and not self._radar_modal_active()
                    and now - self._last_ais_poll >= AIS_REFRESH_SECONDS
                ):
                    self._tick_ais()
                    self._last_ais_poll = now

                grab_seq = self.overhead.grab_seq
                if grab_seq != self._last_grab_seq:
                    self._last_grab_seq = grab_seq
                    if not self._radar_modal_active():
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

                if self._aircraft_photo_redraw and self.screen == SCREEN_FLIGHT:
                    self._aircraft_photo_redraw = False
                    self._safe_draw()

                if self._vessel_photo_redraw and self.screen == SCREEN_FLIGHT:
                    self._vessel_photo_redraw = False
                    self._safe_draw()
                    self._safe_draw()

                if self._weather_redraw_pending and self.screen in (
                    SCREEN_CLOCK,
                    SCREEN_FORECAST,
                ):
                    self._weather_redraw_pending = False
                    self._safe_draw()

                if self._fatal_error:
                    # Don't freeze forever during Wi-Fi setup if a draw glitch set fatal
                    # (e.g. background thread touching pygame).
                    if self.screen == SCREEN_WIFI_SETUP or self._wifi_setup_mode:
                        self._tick_wifi_setup()
                        if not self._fatal_error:
                            continue
                    time.sleep(1.0)
                    continue

                if now < self._boot_until:
                    self._safe_draw()
                    time.sleep(0.05)
                elif self.screen == SCREEN_WIFI_SETUP:
                    self._tick_wifi_setup()
                    if (now - self._last_static_draw) >= 0.5:
                        self._safe_draw()
                        self._last_static_draw = now
                    time.sleep(0.05)
                elif self.screen == SCREEN_RADAR:
                    radar.tick_sweep()
                    frame_ms = theme.SWEEP_FRAME_MS if settings.show_sweep_line() else 50
                    if (time.time() - self._last_radar_draw) * 1000 >= frame_ms:
                        self._safe_draw()
                        self._last_radar_draw = time.time()
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
                        # Match radar cadence so the perimeter countdown crawls smoothly.
                        interval = min(interval, theme.SWEEP_FRAME_MS / 1000.0)
                    if (now - self._last_static_draw) >= interval:
                        self._safe_draw()
                        self._last_static_draw = now
                elif self.screen in (SCREEN_FLIGHT, SCREEN_SETTINGS, SCREEN_DETAILS):
                    interval = (
                        theme.SWEEP_FRAME_MS / 1000.0
                        if self._timeout_remaining_fraction() is not None
                        else 0.25
                    )
                    if (now - self._last_static_draw) >= interval:
                        self._safe_draw()
                        self._last_static_draw = now

                self._tick_timeout()
                self._tick_auto_idle_clock()
                self._tick_off_hours_clock()
                self._apply_brightness()
                # Yield less while the sweep is animating so frames aren't padded to 10ms+.
                if self.screen == SCREEN_RADAR and settings.show_sweep_line():
                    time.sleep(0.001)
                else:
                    time.sleep(0.01)

        except KeyboardInterrupt:
            logger.info("Display stopped by user")
        except Exception:
            logger.exception("Display loop crashed")
            raise
        finally:
            pygame.quit()
