"""Text injection via clipboard + Cmd+V on macOS."""

from __future__ import annotations

import logging
import subprocess

from Quartz import (
    CGEventCreateKeyboardEvent,
    CGEventPost,
    CGEventSetFlags,
    CGEventSourceCreate,
    kCGEventFlagMaskCommand,
    kCGEventSourceStateHIDSystemState,
    kCGHIDEventTap,
)

logger = logging.getLogger(__name__)


def inject_text(text: str) -> None:
    """Copy *text* to clipboard and paste with Cmd+V."""
    if not text:
        return

    # Copy to clipboard via pbcopy
    subprocess.run(
        ["pbcopy"],
        input=text.encode("utf-8"),
        check=True,
        timeout=5,
    )

    # Simulate Cmd+V
    source = CGEventSourceCreate(kCGEventSourceStateHIDSystemState)
    if source is None:
        logger.error("Cannot create CGEvent source for Cmd+V")
        return

    v_keycode = 9  # 'v'

    event_down = CGEventCreateKeyboardEvent(source, v_keycode, True)
    CGEventSetFlags(event_down, kCGEventFlagMaskCommand)
    CGEventPost(kCGHIDEventTap, event_down)

    event_up = CGEventCreateKeyboardEvent(source, v_keycode, False)
    CGEventSetFlags(event_up, kCGEventFlagMaskCommand)
    CGEventPost(kCGHIDEventTap, event_up)

    logger.debug("Injected %d chars via clipboard", len(text))
