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
    CGEventGetIntegerValueField,
    CGEventTapCreate,
    CGEventTapEnable,
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    CFRunLoopGetCurrent,
    CFRunLoopRun,
    CFRunLoopStop,
    kCGEventKeyDown,
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
}

SUPPORTED_HOTKEYS = tuple(HOTKEY_TO_KEYCODE.keys())

# Minimum interval between successive triggers (seconds).
_DEBOUNCE_SECONDS = 0.3

EVENT_TAP_EVENT_TYPES = {kCGEventKeyDown}


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
    """Listens globally for a hotkey and invokes a callback on each press.

    Uses a macOS ``CGEventTap`` in active mode so the configured hotkey is
    intercepted before apps receive it. The event tap's ``CFRunLoop`` runs
    on a daemon thread.

    Parameters
    ----------
    on_hotkey:
        Called (on the run-loop thread) each time the hotkey is pressed.
    hotkey:
        Hotkey name, e.g. ``F5``.
    """

    def __init__(
        self,
        on_hotkey: Callable[[], None],
        *,
        hotkey: str = "F5",
    ) -> None:
        self._on_hotkey = on_hotkey
        self._hotkey_name = normalize_hotkey(hotkey)
        self._hotkey_keycode = parse_hotkey_keycode(self._hotkey_name)
        self._tap = None
        self._run_loop = None
        self._thread: threading.Thread | None = None
        self._last_trigger: float = 0.0
        self._started = threading.Event()
        self._start_error: str | None = None

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

        keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)

        if keycode != self._hotkey_keycode or event_type != kCGEventKeyDown:
            return event

        # Ignore auto-repeat events (key held down).
        if CGEventGetIntegerValueField(event, kCGKeyboardEventAutorepeat):
            return None

        # Debounce rapid presses.
        now = time.monotonic()
        if now - self._last_trigger < _DEBOUNCE_SECONDS:
            return None
        self._last_trigger = now

        logger.debug("%s hotkey triggered", self._hotkey_name)
        try:
            self._on_hotkey()
        except Exception:
            logger.exception("Hotkey callback error")

        return None  # swallow

    # -- public API --------------------------------------------------------

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
