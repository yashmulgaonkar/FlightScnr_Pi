"""FROZEN — Radar touch gesture orchestration (capacitive round panel).

DO NOT change this module or its companions (input_handler.py, pinch_handler.py)
without on-device testing. Run: python3 -m unittest tests.test_gesture_handler -v

Validated behaviour (Jul 2026):
  - One-finger swipe  → screen navigation (MOUSE events)
  - One-finger tap    → open flight at touch point (MOUSE events)
  - Two-finger pinch  → radar range zoom (FINGER events)
  - Single-finger drag must NOT zoom
  - Phantom 2nd contacts during swipe must NOT zoom or eat taps

Architecture:
  Swipes/taps use MOUSEBUTTON* + MOUSEMOTION (_USE_FINGER_EVENTS=False).
  Pinch uses FINGERDOWN / FINGERMOTION / FINGERUP on the radar screen only.
  The mouse button is the source of truth for single-finger gestures:
    - sync_pointer_down() on MOUSEBUTTONDOWN clears stale finger contacts
    - sync_pointer_up() on MOUSEBUTTONUP resets all finger tracking
  Event order per frame: pointer sync → input.handle_event → pinch.handle_event
  Pinch is disabled (allow_zoom=False) while the mouse is down with <2 fingers.
  cancel_gesture() runs only when a scale step is actually applied.
"""

from __future__ import annotations

import pygame

from display.round_touch.input_handler import TouchInput
from display.round_touch.pinch_handler import PinchZoom

# Bump when the frozen contract intentionally changes (see module docstring).
GESTURE_LOGIC_VERSION = 1


class RadarGestureHandler:
    """Coordinates TouchInput + PinchZoom for the radar screen event loop."""

    def __init__(self, touch: TouchInput, pinch: PinchZoom):
        self._touch = touch
        self._pinch = pinch

    @property
    def touch(self) -> TouchInput:
        return self._touch

    @property
    def pinch(self) -> PinchZoom:
        return self._pinch

    def on_pointer_down(self) -> None:
        self._pinch.sync_pointer_down()

    def on_pointer_up(self) -> None:
        self._pinch.sync_pointer_up()

    def handle_input_event(self, event: pygame.event.Event) -> None:
        self._touch.handle_event(event)

    def handle_finger_event(self, event: pygame.event.Event) -> int:
        """Process a FINGER* event on radar. Returns scale index delta."""
        if self._pinch.is_pinching():
            allow_zoom = True
        elif self._touch.is_dragging():
            # Single-finger swipe: ignore phantom 2nd contacts (stuck driver ids).
            allow_zoom = False
        else:
            allow_zoom = self._pinch.finger_count() >= 2
        scale_delta = self._pinch.handle_event(event, allow_zoom=allow_zoom)
        if scale_delta:
            self._touch.cancel_gesture()
        return scale_delta

    @staticmethod
    def is_pointer_down(event: pygame.event.Event) -> bool:
        return event.type == pygame.MOUSEBUTTONDOWN and event.button == 1

    @staticmethod
    def is_pointer_up(event: pygame.event.Event) -> bool:
        return event.type == pygame.MOUSEBUTTONUP and event.button == 1

    @staticmethod
    def is_finger_event(event: pygame.event.Event) -> bool:
        return event.type in (
            pygame.FINGERDOWN,
            pygame.FINGERUP,
            pygame.FINGERMOTION,
        )

    @staticmethod
    def is_touch_event(event: pygame.event.Event) -> bool:
        return RadarGestureHandler.is_finger_event(event) or event.type in (
            pygame.MOUSEBUTTONDOWN,
            pygame.MOUSEBUTTONUP,
            pygame.MOUSEMOTION,
        )
