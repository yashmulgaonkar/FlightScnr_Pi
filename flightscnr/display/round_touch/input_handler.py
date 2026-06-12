"""Touch swipe and tap detection (FlightScnr navigation)."""

import math

import pygame

SWIPE_NONE = 0
SWIPE_UP = 1
SWIPE_DOWN = 2
SWIPE_LEFT = 3
SWIPE_RIGHT = 4

# Resistive panels (e.g. ads7846) emit mouse events; FINGER duplicates break gestures.
_USE_FINGER_EVENTS = False


def _gesture_threshold_px() -> int:
    """Movement below this is a tap; at or above is a swipe."""
    try:
        from display.round_touch import theme
        return max(26, int(theme.SIZE * 0.065))
    except ImportError:
        return 32


class TouchInput:
    """One-finger tap and swipe detection for resistive/capacitive touch panels."""

    def __init__(self):
        self._start = None
        self._drag_end = None
        self._last_motion = None
        self._max_dist = 0.0
        self._pending_swipe = SWIPE_NONE
        self._pending_swipe_start = None
        self._pending_swipe_end = None
        self._pending_tap = None
        self._pending_scroll_dy = 0

    def _clear_pending(self):
        self._pending_swipe = SWIPE_NONE
        self._pending_swipe_start = None
        self._pending_swipe_end = None
        self._pending_tap = None
        self._pending_scroll_dy = 0

    def _track_point(self, pos):
        if self._start is None:
            return
        sx, sy = self._start
        dist = math.hypot(pos[0] - sx, pos[1] - sy)
        self._max_dist = max(self._max_dist, dist)
        self._drag_end = pos
        if self._last_motion is not None and self._max_dist < _gesture_threshold_px():
            dx = pos[0] - self._last_motion[0]
            dy = pos[1] - self._last_motion[1]
            if abs(dy) >= abs(dx):
                self._pending_scroll_dy += dy
        self._last_motion = pos

    def _register_swipe(self, dx: float, dy: float):
        if abs(dx) > abs(dy):
            if dx > 0:
                self._pending_swipe = SWIPE_RIGHT
            elif dx < 0:
                self._pending_swipe = SWIPE_LEFT
        else:
            if dy > 0:
                self._pending_swipe = SWIPE_DOWN
            elif dy < 0:
                self._pending_swipe = SWIPE_UP

    def _finish_pointer(self, sx: float, sy: float, ex: float, ey: float):
        threshold = _gesture_threshold_px()
        total = math.hypot(ex - sx, ey - sy)
        travel = max(self._max_dist, total)

        self._start = None
        self._drag_end = None
        self._last_motion = None
        self._max_dist = 0.0

        if travel < threshold:
            self._pending_swipe = SWIPE_NONE
            self._pending_tap = (int(ex), int(ey))
            return

        self._pending_scroll_dy = 0
        self._pending_tap = None
        self._pending_swipe_start = (int(sx), int(sy))
        self._pending_swipe_end = (int(ex), int(ey))
        self._register_swipe(ex - sx, ey - sy)

    def handle_event(self, event: pygame.event.Event):
        if event.type in (pygame.FINGERDOWN, pygame.FINGERUP) and not _USE_FINGER_EVENTS:
            return

        if event.type == pygame.FINGERDOWN:
            self._clear_pending()
            width = pygame.display.get_surface().get_width()
            height = pygame.display.get_surface().get_height()
            self._start = (event.x * width, event.y * height)
            self._drag_end = self._start
            self._last_motion = self._start
            self._max_dist = 0.0
            return

        if event.type == pygame.FINGERUP and self._start is not None:
            width = pygame.display.get_surface().get_width()
            height = pygame.display.get_surface().get_height()
            sx, sy = self._start
            ex, ey = event.x * width, event.y * height
            self._finish_pointer(sx, sy, ex, ey)
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._clear_pending()
            self._start = event.pos
            self._drag_end = event.pos
            self._last_motion = event.pos
            self._max_dist = 0.0
            return

        if event.type == pygame.MOUSEMOTION and self._start is not None and event.buttons[0]:
            self._track_point(event.pos)
            return

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self._start is not None:
            sx, sy = self._start
            ex, ey = event.pos
            self._finish_pointer(sx, sy, ex, ey)

    def consume_swipe(self) -> int:
        swipe = self._pending_swipe
        self._pending_swipe = SWIPE_NONE
        return swipe

    def consume_tap(self):
        tap = self._pending_tap
        self._pending_tap = None
        return tap

    def consume_scroll_drag(self) -> int:
        """Vertical drag pixels since last call (positive = finger moved down)."""
        dy = self._pending_scroll_dy
        self._pending_scroll_dy = 0
        return dy

    def consume_gesture(self):
        """Return at most one gesture: ('swipe', dir, end_xy) or ('tap', (x, y))."""
        swipe = self.consume_swipe()
        if swipe != SWIPE_NONE:
            self._pending_tap = None
            start = self._pending_swipe_start
            end = self._pending_swipe_end
            self._pending_swipe_start = None
            self._pending_swipe_end = None
            return ("swipe", swipe, end, start)
        tap = self.consume_tap()
        if tap:
            return ("tap", tap)
        return None
