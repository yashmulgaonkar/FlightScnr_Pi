"""Touch swipe and tap detection (FlightScnr navigation).

FROZEN — see gesture_handler.py and tests/test_gesture_handler.py.
Swipes/taps use MOUSE events only (_USE_FINGER_EVENTS=False).
"""

import math

import pygame

SWIPE_NONE = 0
SWIPE_UP = 1
SWIPE_DOWN = 2
SWIPE_LEFT = 3
SWIPE_RIGHT = 4

# Capacitive panels (Waveshare DSI) emit FINGER events; resistive panels use mouse.
def _use_finger_events() -> bool:
    import os

    raw = os.environ.get("TOUCH_USE_FINGER_EVENTS", "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


_USE_FINGER_EVENTS = _use_finger_events()


def use_finger_events() -> bool:
    return _USE_FINGER_EVENTS


def _gesture_threshold_px() -> int:
    """Movement below this is a tap; at or above is a swipe."""
    try:
        from display.round_touch import theme
        return max(26, int(theme.SIZE * 0.065))
    except ImportError:
        return 32


def gesture_threshold_px() -> int:
    return _gesture_threshold_px()


def _logical_pos(pos) -> tuple[int, int]:
    from display.round_touch import rotation

    return rotation.to_logical(pos[0], pos[1])


class TouchInput:
    """One-finger tap and swipe detection for resistive/capacitive touch panels."""

    def __init__(self):
        self._start = None
        self._drag_end = None
        self._last_motion = None
        self._max_dist = 0.0
        self._active_fid: int | None = None
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
        self._active_fid = None

        if travel < threshold:
            self._pending_swipe = SWIPE_NONE
            self._pending_tap = (int(ex), int(ey))
            return

        self._pending_scroll_dy = 0
        self._pending_tap = None
        self._pending_swipe_start = (int(sx), int(sy))
        self._pending_swipe_end = (int(ex), int(ey))
        self._register_swipe(ex - sx, ey - sy)

    def is_dragging(self) -> bool:
        """True while a mouse-button gesture is in progress."""
        return self._start is not None

    def active_finger_id(self) -> int | None:
        return self._active_fid

    def blocks_pinch(self) -> bool:
        """True once single-finger movement looks like a swipe (not a pinch setup)."""
        if self._start is None:
            return False
        return self._max_dist >= _gesture_threshold_px() * 0.3

    def cancel_gesture(self):
        """Drop in-progress single-touch tracking (e.g. when a pinch starts)."""
        self._start = None
        self._drag_end = None
        self._last_motion = None
        self._max_dist = 0.0
        self._active_fid = None
        self._clear_pending()

    def handle_event(self, event: pygame.event.Event):
        if _USE_FINGER_EVENTS and event.type in (
            pygame.MOUSEBUTTONDOWN,
            pygame.MOUSEBUTTONUP,
            pygame.MOUSEMOTION,
        ):
            return

        if event.type in (pygame.FINGERDOWN, pygame.FINGERUP) and not _USE_FINGER_EVENTS:
            return

        if event.type == pygame.FINGERDOWN:
            fid = int(event.finger_id)
            if self._start is not None and self._active_fid not in (None, fid):
                # Stuck driver finger id — hand off to the real touch.
                if self._max_dist < _gesture_threshold_px() * 0.3:
                    self.cancel_gesture()
                else:
                    return
            self._clear_pending()
            width = pygame.display.get_surface().get_width()
            height = pygame.display.get_surface().get_height()
            self._active_fid = fid
            self._start = _logical_pos((event.x * width, event.y * height))
            self._drag_end = self._start
            self._last_motion = self._start
            self._max_dist = 0.0
            return

        if event.type == pygame.FINGERMOTION and self._start is not None:
            if int(event.finger_id) != self._active_fid:
                return
            width = pygame.display.get_surface().get_width()
            height = pygame.display.get_surface().get_height()
            self._track_point(_logical_pos((event.x * width, event.y * height)))
            return

        if event.type == pygame.FINGERUP and self._start is not None:
            if int(event.finger_id) != self._active_fid:
                return
            width = pygame.display.get_surface().get_width()
            height = pygame.display.get_surface().get_height()
            sx, sy = self._start
            ex, ey = _logical_pos((event.x * width, event.y * height))
            self._finish_pointer(sx, sy, ex, ey)
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._clear_pending()
            self._start = _logical_pos(event.pos)
            self._drag_end = self._start
            self._last_motion = self._start
            self._max_dist = 0.0
            return

        if event.type == pygame.MOUSEMOTION and self._start is not None and event.buttons[0]:
            self._track_point(_logical_pos(event.pos))
            return

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self._start is not None:
            sx, sy = self._start
            ex, ey = _logical_pos(event.pos)
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
