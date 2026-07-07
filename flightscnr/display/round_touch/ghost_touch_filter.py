"""Suppress stuck capacitive-panel jitter from reaching gesture navigation."""

from __future__ import annotations

import logging
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

import pygame

logger = logging.getLogger("flightscnr.display")

_STUCK_MS = 280
_HOT_STUCK_MS = 90
_QUIET_MS = 200


def enabled() -> bool:
    raw = os.environ.get("GHOST_TOUCH_FILTER", "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _logical_xy(event: pygame.event.Event) -> tuple[int, int]:
    from display.round_touch import rotation

    surface = pygame.display.get_surface()
    if surface is None:
        return 0, 0
    width, height = surface.get_size()
    if event.type in (pygame.FINGERDOWN, pygame.FINGERUP, pygame.FINGERMOTION):
        x = event.x * width
        y = event.y * height
    else:
        x, y = event.pos
    return rotation.to_logical(x, y)


def _jitter_radius() -> float:
    from display.round_touch import theme

    return float(theme.s(36))


def _in_hot_corner(pos: tuple[int, int]) -> bool:
    """Small bottom-left zone where this panel reports stuck ghost contacts."""
    from display.round_touch import theme

    margin_x = theme.s(108)
    margin_y = theme.s(22)
    return pos[0] < margin_x and pos[1] > theme.SIZE - margin_y


def _is_finger_event(event: pygame.event.Event) -> bool:
    return event.type in (pygame.FINGERDOWN, pygame.FINGERUP, pygame.FINGERMOTION)


def _is_pointer_event(event: pygame.event.Event) -> bool:
    return event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION)


@dataclass
class _FingerTrack:
    anchor: tuple[int, int]
    last: tuple[int, int]
    max_radius: float = 0.0
    path_length: float = 0.0
    down_at: float = 0.0
    phantom: bool = False
    orphan: bool = False


class GhostTouchFilter:
    """Drop micro-jitter and stuck contacts before TouchInput sees them."""

    def __init__(self) -> None:
        self._fingers: dict[int, _FingerTrack] = {}
        self._tracking = False
        self._phantom = False
        self._anchor: tuple[int, int] | None = None
        self._last: tuple[int, int] | None = None
        self._max_radius = 0.0
        self._path_length = 0.0
        self._down_at = 0.0
        self._quiet_until = 0.0
        self._logged_phantom = False

    def _maybe_cancel(
        self,
        cancel_gesture: Callable[[], None],
        is_dragging: Callable[[], bool] | None,
    ) -> None:
        if is_dragging is not None and is_dragging():
            return
        cancel_gesture()

    def _hot_corner_blocked(self, pos: tuple[int, int], now: float) -> bool:
        if not _in_hot_corner(pos):
            return False
        return now < self._quiet_until

    def _reset_pointer(self) -> None:
        self._tracking = False
        self._phantom = False
        self._anchor = None
        self._last = None
        self._max_radius = 0.0
        self._path_length = 0.0
        self._down_at = 0.0
        self._logged_phantom = False

    def _log_phantom(self, pos: tuple[int, int], radius: float, reason: str) -> None:
        if not self._logged_phantom:
            self._logged_phantom = True
            logger.info(
                "ghost touch filtered at logical=(%d,%d) radius=%.1f (%s)",
                pos[0],
                pos[1],
                radius,
                reason,
            )

    def _drop_phantom_finger(self, fid: int) -> None:
        self._fingers.pop(fid, None)

    def _mark_finger_phantom(
        self,
        fid: int,
        cancel_gesture: Callable[[], None],
        is_dragging: Callable[[], bool] | None,
        reason: str,
    ) -> None:
        track = self._fingers.get(fid)
        if track is None or track.phantom:
            return
        track.phantom = True
        self._log_phantom(track.anchor, track.max_radius, reason)
        self._drop_phantom_finger(fid)
        self._maybe_cancel(cancel_gesture, is_dragging)

    def _update_finger_motion(self, fid: int, pos: tuple[int, int], now: float) -> None:
        track = self._fingers[fid]
        if track.last != pos:
            track.path_length += math.hypot(pos[0] - track.last[0], pos[1] - track.last[1])
            track.last = pos
        track.max_radius = max(
            track.max_radius,
            math.hypot(pos[0] - track.anchor[0], pos[1] - track.anchor[1]),
        )
        jitter = _jitter_radius()
        held_ms = (now - track.down_at) * 1000.0
        hot = _in_hot_corner(track.anchor)
        stuck_ms = _HOT_STUCK_MS if hot else _STUCK_MS
        if track.max_radius < jitter and held_ms >= stuck_ms:
            track.phantom = True
        if hot and track.max_radius < jitter and track.path_length > jitter * 1.6:
            track.phantom = True

    def _allow_finger(
        self,
        event: pygame.event.Event,
        cancel_gesture: Callable[[], None],
        is_dragging: Callable[[], bool] | None,
        now: float,
    ) -> bool:
        fid = int(event.finger_id)
        pos = _logical_xy(event)

        if event.type == pygame.FINGERDOWN:
            if _in_hot_corner(pos):
                self._log_phantom(pos, 0.0, "finger down in hot corner")
                self._quiet_until = now + (_QUIET_MS / 1000.0)
                self._maybe_cancel(cancel_gesture, is_dragging)
                return False
            self._fingers[fid] = _FingerTrack(anchor=pos, last=pos, down_at=now)
            return True

        if event.type == pygame.FINGERMOTION:
            track = self._fingers.get(fid)
            if track is None:
                if not _in_hot_corner(pos):
                    return True
                self._log_phantom(pos, 0.0, "orphan finger motion")
                self._quiet_until = now + (_QUIET_MS / 1000.0)
                self._maybe_cancel(cancel_gesture, is_dragging)
                return False
            self._update_finger_motion(fid, pos, now)
            if track.phantom:
                self._drop_phantom_finger(fid)
                return False
            if _in_hot_corner(track.anchor) and track.max_radius < _jitter_radius():
                self._mark_finger_phantom(
                    fid, cancel_gesture, is_dragging, "stuck hot-corner finger"
                )
                return False
            return True

        if event.type == pygame.FINGERUP:
            track = self._fingers.pop(fid, None)
            if track is None:
                return False
            if track.phantom or track.orphan:
                self._quiet_until = now + (_QUIET_MS / 1000.0)
                self._maybe_cancel(cancel_gesture, is_dragging)
                return False
            return True

        return True

    def _mark_pointer_phantom(
        self,
        cancel_gesture: Callable[[], None],
        is_dragging: Callable[[], bool] | None,
        reason: str,
    ) -> None:
        if not self._phantom:
            self._phantom = True
            anchor = self._anchor or self._last or (0, 0)
            self._log_phantom(anchor, self._max_radius, reason)
        self._maybe_cancel(cancel_gesture, is_dragging)

    def _update_pointer_motion(self, pos: tuple[int, int], now: float) -> None:
        if self._last is not None and self._last != pos:
            self._path_length += math.hypot(
                pos[0] - self._last[0], pos[1] - self._last[1]
            )
        self._last = pos
        if self._anchor is not None:
            self._max_radius = max(
                self._max_radius,
                math.hypot(pos[0] - self._anchor[0], pos[1] - self._anchor[1]),
            )

    def _allow_pointer(
        self,
        event: pygame.event.Event,
        cancel_gesture: Callable[[], None],
        is_dragging: Callable[[], bool] | None,
        now: float,
    ) -> bool:
        pos = _logical_xy(event) if event.type != pygame.MOUSEMOTION or event.buttons[0] else None
        if pos is not None and self._hot_corner_blocked(pos, now):
            if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                self._reset_pointer()
            return False

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = _logical_xy(event)
            if _in_hot_corner(pos):
                self._mark_pointer_phantom(
                    cancel_gesture, is_dragging, "pointer down in hot corner"
                )
                self._tracking = True
                self._anchor = pos
                self._last = pos
                self._down_at = now
                return False
            self._tracking = True
            self._phantom = False
            self._anchor = pos
            self._last = pos
            self._max_radius = 0.0
            self._path_length = 0.0
            self._down_at = now
            self._logged_phantom = False
            return True

        if event.type == pygame.MOUSEMOTION and event.buttons[0]:
            pos = _logical_xy(event)
            if not self._tracking:
                if _in_hot_corner(pos):
                    self._mark_pointer_phantom(
                        cancel_gesture, is_dragging, "pointer motion without down"
                    )
                    return False
                return True
            self._update_pointer_motion(pos, now)
            jitter = _jitter_radius()
            held_ms = (now - self._down_at) * 1000.0 if self._down_at else 0.0
            anchor = self._anchor or pos
            hot = _in_hot_corner(anchor)
            stuck_ms = _HOT_STUCK_MS if hot else _STUCK_MS
            if held_ms >= stuck_ms and self._max_radius < jitter:
                self._mark_pointer_phantom(cancel_gesture, is_dragging, "stuck pointer")
            if hot and self._max_radius < jitter and self._path_length > jitter * 1.6:
                self._mark_pointer_phantom(
                    cancel_gesture, is_dragging, "hot-corner wander"
                )
            if self._phantom:
                return False
            return True

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            pos = _logical_xy(event)
            if self._tracking:
                self._update_pointer_motion(pos, now)
            jitter = _jitter_radius()
            held_ms = (now - self._down_at) * 1000.0 if self._down_at else 0.0
            anchor = self._anchor or pos
            micro = self._max_radius < jitter
            hot = _in_hot_corner(anchor)
            if self._phantom or (self._tracking and micro and (held_ms >= _STUCK_MS or hot)):
                self._maybe_cancel(cancel_gesture, is_dragging)
                self._quiet_until = now + (_QUIET_MS / 1000.0)
                self._reset_pointer()
                return False
            self._reset_pointer()
            return True

        return True

    def allow(
        self,
        event: pygame.event.Event,
        cancel_gesture: Callable[[], None],
        is_dragging: Callable[[], bool] | None = None,
    ) -> bool:
        """Return True when the event should reach gesture handling."""
        if not enabled():
            return True

        now = time.time()
        if _is_finger_event(event):
            return self._allow_finger(event, cancel_gesture, is_dragging, now)
        if _is_pointer_event(event):
            return self._allow_pointer(event, cancel_gesture, is_dragging, now)
        return True
