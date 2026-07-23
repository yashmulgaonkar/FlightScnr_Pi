"""Touch swipe and tap detection (FlightScnr navigation).

FROZEN — see gesture_handler.py and tests/test_gesture_handler.py.

Default path: MOUSEBUTTON* / MOUSEMOTION (works under Bookworm labwc/Xwayland,
which pointer-emulates touch and never sends FINGER*).

Optional TOUCH_USE_FINGER_EVENTS=True prefers FINGER* when the stack actually
delivers them (some capacitive DSI + SDL paths). Until a FINGER event is seen,
mouse events are still accepted so Xwayland installs are not dead (issue #14).
"""

import logging
import math
import os

import pygame

logger = logging.getLogger("flightscnr.display")

SWIPE_NONE = 0
SWIPE_UP = 1
SWIPE_DOWN = 2
SWIPE_LEFT = 3
SWIPE_RIGHT = 4


def _env_prefer_finger_events() -> bool:
    raw = os.environ.get("TOUCH_USE_FINGER_EVENTS", "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


# Configured preference from env (True = prefer FINGER* when available).
_USE_FINGER_EVENTS = _env_prefer_finger_events()
# Latched once any FINGER* event arrives this process.
_finger_events_seen = False
_logged_mouse_fallback = False


def prefer_finger_events() -> bool:
    """True when env asks for FINGER*-based single-touch handling."""
    return _USE_FINGER_EVENTS


def finger_events_seen() -> bool:
    """True once any SDL FINGER* event has arrived this process."""
    return _finger_events_seen


def use_finger_events() -> bool:
    """True when single-touch should use the FINGER* path right now.

    Stays False until a FINGER event is observed, even if env prefers fingers,
    so Bookworm + Xwayland (mouse-only) keeps working with a True env value.
    """
    return _USE_FINGER_EVENTS and _finger_events_seen


def _note_finger_event() -> None:
    global _finger_events_seen
    if not _finger_events_seen:
        _finger_events_seen = True
        if _USE_FINGER_EVENTS:
            logger.info(
                "Touch: FINGER events detected — using finger path for taps/swipes"
            )


def _note_mouse_fallback() -> None:
    global _logged_mouse_fallback
    if _logged_mouse_fallback or not _USE_FINGER_EVENTS or _finger_events_seen:
        return
    _logged_mouse_fallback = True
    logger.info(
        "Touch: mouse events without FINGER* (typical under Xwayland) — "
        "using mouse path for taps/swipes. Pinch-to-zoom needs SDL FINGER* "
        "multi-touch and will stay unavailable until those arrive "
        "(see README / GitHub issue #21). "
        "Set TOUCH_USE_FINGER_EVENTS=False in /etc/flightscnr.env to silence "
        "this if env is True (GitHub issue #14)."
    )


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

    def drag_pos(self) -> tuple[int, int] | None:
        """Current pointer position while a drag is in progress, else None."""
        if self._start is None:
            return None
        if self._drag_end is not None:
            return (int(self._drag_end[0]), int(self._drag_end[1]))
        return (int(self._start[0]), int(self._start[1]))

    def cancel_gesture(self):
        """Drop in-progress single-touch tracking (e.g. when a pinch starts)."""
        self._start = None
        self._drag_end = None
        self._last_motion = None
        self._max_dist = 0.0
        self._active_fid = None
        self._clear_pending()

    def handle_event(self, event: pygame.event.Event):
        if event.type in (pygame.FINGERDOWN, pygame.FINGERMOTION, pygame.FINGERUP):
            _note_finger_event()

        # Drop redundant mouse down/motion only after FINGER* has proven available.
        # Under Xwayland, FINGER* never arrives — keep mouse so taps are not dead.
        if use_finger_events() and event.type in (
            pygame.MOUSEBUTTONDOWN,
            pygame.MOUSEMOTION,
        ):
            return

        if use_finger_events() and event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            # Waveshare DSI reports both FINGER* and MOUSE*; release is often mouse-only.
            if self._start is not None:
                sx, sy = self._start
                if self._drag_end is not None:
                    ex, ey = self._drag_end
                else:
                    ex, ey = _logical_pos(event.pos)
                self._finish_pointer(sx, sy, ex, ey)
            return

        if event.type in (pygame.FINGERDOWN, pygame.FINGERUP) and not prefer_finger_events():
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
            _note_mouse_fallback()
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
