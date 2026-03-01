"""Global hotkey listener for macOS using CGEventTap.

Requires the Accessibility permission to be granted in
System Preferences → Privacy & Security → Accessibility.
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

# macOS virtual key code for F5.
F5_KEYCODE = 96



# Minimum interval between successive triggers (seconds).
_DEBOUNCE_SECONDS = 0.3


class GlobalHotkeyListener:
    """Listens globally for F5 key-down events and invokes a callback.

    Uses a macOS ``CGEventTap`` in listen-only mode so that the F5
    keypress is still delivered to the focused application.  The event
    tap's ``CFRunLoop`` runs on a daemon thread.

    Parameters
    ----------
    on_hotkey:
        Called (on the run-loop thread) each time F5 is pressed.
    """

    def __init__(self, on_hotkey: Callable[[], None]) -> None:
        self._on_hotkey = on_hotkey
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

        if keycode != F5_KEYCODE or event_type != kCGEventKeyDown:
            return event

        # Ignore auto-repeat events (holding F5 down).
        if CGEventGetIntegerValueField(event, kCGKeyboardEventAutorepeat):
            return event

        # Debounce rapid presses.
        now = time.monotonic()
        if now - self._last_trigger < _DEBOUNCE_SECONDS:
            return event
        self._last_trigger = now

        logger.debug("F5 hotkey triggered")
        try:
            self._on_hotkey()
        except Exception:
            logger.exception("Hotkey callback error")

        return event

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
            # Thread failed to spawn — don't leave stale state.
            raise RuntimeError("Failed to start hotkey listener thread")

        self._thread = thread

        # Wait for the run-loop thread to signal success or failure.
        if not self._started.wait(timeout=self._START_TIMEOUT):
            # Timed out — thread is stuck or died before signalling.
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
            # Listen for regular key-down events only.  On Mac laptops
            # where function keys default to media-key mode, the user
            # must either hold Fn+F5 or enable "Use F1, F2, etc. keys
            # as standard function keys" in System Settings → Keyboard.
            mask = 1 << kCGEventKeyDown

            # 1 = kCGEventTapOptionListenOnly — we observe but never block input.
            tap = CGEventTapCreate(
                kCGSessionEventTap,
                kCGHeadInsertEventTap,
                1,  # listen-only
                mask,
                self._callback,
                None,
            )

            if tap is None:
                self._start_error = (
                    "Failed to create CGEventTap — "
                    "grant Accessibility permission in System Preferences → "
                    "Privacy & Security → Accessibility"
                )
                logger.error(self._start_error)
                return

            self._tap = tap
            source = CFMachPortCreateRunLoopSource(None, tap, 0)
            self._run_loop = CFRunLoopGetCurrent()
            CFRunLoopAddSource(self._run_loop, source, kCFRunLoopDefaultMode)
            CGEventTapEnable(tap, True)

            logger.info("Global hotkey listener active (F5)")
        except Exception as exc:
            self._start_error = f"Hotkey listener failed during setup: {exc}"
            logger.exception("Hotkey listener failed during setup")
            return
        finally:
            # Always unblock start(), whether we succeeded or failed.
            self._started.set()

        # Enter the run loop only after signalling success.
        CFRunLoopRun()
        logger.debug("Hotkey run loop exited")
