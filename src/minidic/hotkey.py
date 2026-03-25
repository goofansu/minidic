"""Global hotkey listener for macOS using CGEventTap.

Requires the Accessibility permission to be granted in
System Settings → Privacy & Security → Accessibility.

The listener uses an active event tap (not listen-only), so the configured
hotkey is swallowed globally and does not reach the focused application.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from Quartz import (
    CGEventGetFlags,
    CGEventGetIntegerValueField,
    CGEventTapCreate,
    CGEventTapEnable,
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    CFRunLoopGetCurrent,
    CFRunLoopRun,
    CFRunLoopStop,
    kCGEventFlagsChanged,
    kCGEventKeyDown,
    kCGEventKeyUp,
    kCGEventLeftMouseDown,
    kCGEventLeftMouseDragged,
    kCGEventLeftMouseUp,
    kCGEventOtherMouseDown,
    kCGEventOtherMouseDragged,
    kCGEventOtherMouseUp,
    kCGEventRightMouseDown,
    kCGEventRightMouseDragged,
    kCGEventRightMouseUp,
    kCGEventScrollWheel,
    kCGHeadInsertEventTap,
    kCGKeyboardEventAutorepeat,
    kCGKeyboardEventKeycode,
    kCGSessionEventTap,
    kCFRunLoopDefaultMode,
)

logger = logging.getLogger(__name__)

HOTKEY_TO_KEYCODE = {
    "F1": 122,
    "F2": 120,
    "F3": 99,
    "F4": 118,
    "F5": 96,
    "F6": 97,
    "F7": 98,
    "F8": 100,
    "F9": 101,
    "F10": 109,
    "F11": 103,
    "F12": 111,
    "RIGHT_COMMAND": 54,
    "RIGHT_OPTION": 61,
    "RIGHT_SHIFT": 60,
    "RIGHT_CONTROL": 62,
}

SUPPORTED_HOTKEYS = tuple(HOTKEY_TO_KEYCODE.keys())
MODIFIER_KEYCODES = {54, 60, 61, 62}
MODIFIER_DEVICE_FLAG = {
    54: 0x0010,
    60: 0x0004,
    61: 0x0040,
    62: 0x2000,
}
MODIFIER_MOUSE_EVENT_TYPES = {
    kCGEventLeftMouseDown,
    kCGEventLeftMouseUp,
    kCGEventLeftMouseDragged,
    kCGEventRightMouseDown,
    kCGEventRightMouseUp,
    kCGEventRightMouseDragged,
    kCGEventOtherMouseDown,
    kCGEventOtherMouseUp,
    kCGEventOtherMouseDragged,
    kCGEventScrollWheel,
}
MODIFIER_COMBO_EVENT_TYPES = {
    kCGEventKeyDown,
    kCGEventFlagsChanged,
    *MODIFIER_MOUSE_EVENT_TYPES,
}
EVENT_TAP_EVENT_TYPES = {
    kCGEventKeyDown,
    kCGEventKeyUp,
    kCGEventFlagsChanged,
    *MODIFIER_MOUSE_EVENT_TYPES,
}


# Minimum interval between successive press callbacks (seconds).
DEFAULT_PRESS_DEBOUNCE_SECONDS = 0.3


def normalize_hotkey(value: str) -> str:
    """Normalize a user hotkey string (e.g. 'f5' -> 'F5')."""
    return value.strip().upper()


def parse_hotkey_keycode(value: str) -> int:
    """Parse hotkey name and return macOS virtual keycode.

    Raises
    ------
    ValueError
        If the hotkey name is unsupported.
    """
    hotkey = normalize_hotkey(value)
    try:
        return HOTKEY_TO_KEYCODE[hotkey]
    except KeyError as exc:
        supported = ", ".join(SUPPORTED_HOTKEYS)
        raise ValueError(f"Unsupported hotkey '{value}'. Supported: {supported}") from exc


class GlobalHotkeyListener:
    """Listens globally for a hotkey and invokes press/release callbacks.

    Uses a macOS ``CGEventTap`` in active mode so the configured hotkey is
    intercepted before apps receive it. The event tap's ``CFRunLoop`` runs
    on a daemon thread.

    Parameters
    ----------
    on_press:
        Called (on the run-loop thread) when the hotkey is pressed.
    on_release:
        Called (on the run-loop thread) when the hotkey is released.
    hotkey:
        Hotkey name, e.g. ``F5`` or ``RIGHT_COMMAND``.
    press_debounce_seconds:
        Minimum interval between successive press callbacks.
    """

    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        *,
        hotkey: str = "F5",
        press_debounce_seconds: float = DEFAULT_PRESS_DEBOUNCE_SECONDS,
        modifier_press_on_release: bool = False,
    ) -> None:
        self._on_press = on_press
        self._on_release = on_release
        self._hotkey_name = normalize_hotkey(hotkey)
        self._hotkey_keycode = parse_hotkey_keycode(self._hotkey_name)
        self._tap = None
        self._run_loop = None
        self._thread: threading.Thread | None = None
        self._last_press: float = 0.0
        self._started = threading.Event()
        self._start_error: str | None = None
        self._pressed = False
        self._press_fired = False
        self._modifier_combo_active = False
        self._press_timer: threading.Timer | None = None
        self._state_lock = threading.Lock()
        self._press_debounce_seconds = max(0.0, press_debounce_seconds)
        self._modifier_press_on_release = modifier_press_on_release

    # -- CGEventTap callback -----------------------------------------------

    def _callback(
        self,
        proxy: object,
        event_type: int,
        event: object,
        refcon: object,
    ) -> object:
        # Handle tap-disabled events (macOS may disable the tap).
        _TAP_DISABLED_BY_TIMEOUT = 0xFFFFFFFE
        if event_type == _TAP_DISABLED_BY_TIMEOUT:
            logger.warning("Event tap was disabled by timeout — re-enabling")
            if self._tap is not None:
                CGEventTapEnable(self._tap, True)
            return event

        if (
            self._hotkey_keycode in MODIFIER_KEYCODES
            and event_type in MODIFIER_MOUSE_EVENT_TYPES
        ):
            self._note_modifier_combo(event_type)
            return event

        keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
        if self._hotkey_keycode in MODIFIER_KEYCODES and keycode != self._hotkey_keycode:
            self._note_modifier_combo(event_type)
            return event

        if keycode != self._hotkey_keycode:
            return event

        if keycode in MODIFIER_KEYCODES:
            return self._handle_modifier_event(event_type, event)
        return self._handle_standard_key_event(event_type, event)

    def _handle_standard_key_event(self, event_type: int, event: object) -> object:
        if event_type == kCGEventKeyDown:
            if CGEventGetIntegerValueField(event, kCGKeyboardEventAutorepeat):
                return None
            if not self._should_fire_press():
                return None
            self._pressed = True
            logger.debug("%s hotkey pressed", self._hotkey_name)
            self._safe_invoke(self._on_press, "press")
            return None

        if event_type == kCGEventKeyUp:
            self._pressed = False
            logger.debug("%s hotkey released", self._hotkey_name)
            self._safe_invoke(self._on_release, "release")
            return None

        return event

    def _handle_modifier_event(self, event_type: int, event: object) -> object:
        if event_type == kCGEventFlagsChanged:
            device_flags = CGEventGetFlags(event) & 0xFFFF
            is_pressed = bool(device_flags & MODIFIER_DEVICE_FLAG[self._hotkey_keycode])

            if is_pressed:
                self._handle_modifier_press()
            else:
                self._handle_modifier_release()

        return None

    def _handle_modifier_press(self) -> None:
        with self._state_lock:
            if self._pressed or not self._should_fire_press():
                return
            self._pressed = True
            self._press_fired = False
            self._modifier_combo_active = False
            modifier_press_on_release = self._modifier_press_on_release

        if modifier_press_on_release:
            return
        self._schedule_modifier_press()

    def _handle_modifier_release(self) -> None:
        should_tap = False
        should_release = False

        with self._state_lock:
            self._cancel_press_timer_locked()
            if self._modifier_press_on_release:
                should_tap = self._pressed and not self._modifier_combo_active
            else:
                should_release = (
                    self._pressed and self._press_fired and not self._modifier_combo_active
                )
            self._pressed = False
            self._press_fired = False
            self._modifier_combo_active = False

        if should_tap:
            logger.debug("%s hotkey tapped", self._hotkey_name)
            self._safe_invoke(self._on_press, "press")
            return

        if should_release:
            logger.debug("%s hotkey released", self._hotkey_name)
            self._safe_invoke(self._on_release, "release")

    def _schedule_modifier_press(self) -> None:
        self._cancel_press_timer()
        if self._press_debounce_seconds == 0:
            self._fire_modifier_press()
            return
        timer = threading.Timer(self._press_debounce_seconds, self._fire_modifier_press)
        timer.daemon = True
        self._press_timer = timer
        timer.start()

    def _fire_modifier_press(self) -> None:
        with self._state_lock:
            self._press_timer = None
            if not self._pressed or self._modifier_combo_active or self._press_fired:
                return
            self._press_fired = True

        logger.debug("%s hotkey pressed", self._hotkey_name)
        self._safe_invoke(self._on_press, "press")

    def _note_modifier_combo(self, event_type: int) -> None:
        if event_type not in MODIFIER_COMBO_EVENT_TYPES:
            return

        should_release = False
        with self._state_lock:
            if not self._pressed or self._modifier_combo_active:
                return
            self._modifier_combo_active = True
            self._cancel_press_timer_locked()
            if self._press_fired:
                self._pressed = False
                self._press_fired = False
                should_release = True

        if should_release:
            logger.debug("%s hotkey cancelled by key combo", self._hotkey_name)
            self._safe_invoke(self._on_release, "release")

    def _cancel_press_timer(self) -> None:
        with self._state_lock:
            self._cancel_press_timer_locked()

    def _cancel_press_timer_locked(self) -> None:
        if self._press_timer is None:
            return
        self._press_timer.cancel()
        self._press_timer = None

    def _should_fire_press(self) -> bool:
        now = time.monotonic()
        if now - self._last_press < self._press_debounce_seconds:
            return False
        self._last_press = now
        return True

    def _safe_invoke(self, callback: Callable[[], None], phase: str) -> None:
        try:
            callback()
        except Exception:
            logger.exception("Hotkey %s callback error", phase)

    # -- public API --------------------------------------------------------

    # Generous upper bound for event-tap setup; the Quartz calls normally
    # complete in milliseconds so 10 s means something is seriously wrong.
    _START_TIMEOUT = 10.0

    def start(self) -> None:
        """Start listening in a daemon thread.

        Raises ``RuntimeError`` if the CGEventTap cannot be created
        (e.g. Accessibility permission not granted) or if the listener
        thread fails to initialise within the timeout.
        """
        if self._thread is not None:
            return
        self._started.clear()
        self._start_error = None
        thread = threading.Thread(
            target=self._run, name="hotkey-listener", daemon=True
        )
        try:
            thread.start()
        except Exception:
            raise RuntimeError("Failed to start hotkey listener thread")

        self._thread = thread

        if not self._started.wait(timeout=self._START_TIMEOUT):
            self._thread = None
            alive = thread.is_alive()
            raise RuntimeError(
                "Hotkey listener thread did not initialise within "
                f"{self._START_TIMEOUT}s (thread alive={alive})"
            )
        if self._start_error is not None:
            self._thread = None
            raise RuntimeError(self._start_error)

    def stop(self) -> None:
        """Disable the event tap and stop the run loop."""
        with self._state_lock:
            self._cancel_press_timer_locked()
            self._pressed = False
            self._press_fired = False
            self._modifier_combo_active = False
        if self._tap is not None:
            CGEventTapEnable(self._tap, False)
            self._tap = None
        if self._run_loop is not None:
            CFRunLoopStop(self._run_loop)
            self._run_loop = None
        self._thread = None

    # -- internals ---------------------------------------------------------

    def _run(self) -> None:
        """Create the event tap and enter the CFRunLoop (blocks)."""
        try:
            mask = 0
            for event_type in EVENT_TAP_EVENT_TYPES:
                mask |= 1 << event_type

            # 0 = kCGEventTapOptionDefault — active tap; callback may swallow
            # events by returning None.
            tap = CGEventTapCreate(
                kCGSessionEventTap,
                kCGHeadInsertEventTap,
                0,
                mask,
                self._callback,
                None,
            )

            if tap is None:
                self._start_error = (
                    "Failed to create CGEventTap — "
                    "grant Accessibility permission in System Settings → "
                    "Privacy & Security → Accessibility"
                )
                logger.error(self._start_error)
                return

            self._tap = tap
            source = CFMachPortCreateRunLoopSource(None, tap, 0)
            self._run_loop = CFRunLoopGetCurrent()
            CFRunLoopAddSource(self._run_loop, source, kCFRunLoopDefaultMode)
            CGEventTapEnable(tap, True)

            logger.info("Global hotkey listener active (%s)", self._hotkey_name)
        except Exception as exc:
            self._start_error = f"Hotkey listener failed during setup: {exc}"
            logger.exception("Hotkey listener failed during setup")
            return
        finally:
            self._started.set()

        CFRunLoopRun()
        logger.debug("Hotkey run loop exited")
