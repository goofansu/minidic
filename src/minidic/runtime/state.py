"""Transient daemon state persisted for UI/process coordination."""

from __future__ import annotations

from pathlib import Path

_STATE_DIR = Path.home() / ".local" / "state" / "minidic"
DAEMON_STATE_FILE = _STATE_DIR / "daemon.state"

_VALID_STATES = {"idle", "recording", "transcribing"}


def read_runtime_state() -> str:
    try:
        state = DAEMON_STATE_FILE.read_text().strip().lower()
    except OSError:
        return "idle"
    return state if state in _VALID_STATES else "idle"


def write_runtime_state(state: str) -> None:
    DAEMON_STATE_FILE.write_text(state)


def clear_runtime_state() -> None:
    DAEMON_STATE_FILE.unlink(missing_ok=True)
