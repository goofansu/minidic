"""Transient daemon state persisted for UI/process coordination."""

from __future__ import annotations

from pathlib import Path

_STATE_DIR = Path.home() / ".local" / "state" / "minidic"
DAEMON_STATE_FILE = _STATE_DIR / "daemon.state"

_VALID_STATES = {"idle", "recording", "transcribing", "error"}

DAEMON_ERROR_FILE = _STATE_DIR / "daemon.error"


def read_runtime_state() -> str:
    try:
        state = DAEMON_STATE_FILE.read_text().strip().lower()
    except OSError:
        return "idle"
    return state if state in _VALID_STATES else "idle"


def read_runtime_error() -> str:
    try:
        return DAEMON_ERROR_FILE.read_text().strip()
    except OSError:
        return ""


def write_runtime_state(state: str) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    DAEMON_STATE_FILE.write_text(state)


def write_runtime_error(message: str) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    DAEMON_ERROR_FILE.write_text(message)


def clear_runtime_error() -> None:
    DAEMON_ERROR_FILE.unlink(missing_ok=True)


def clear_runtime_state() -> None:
    DAEMON_STATE_FILE.unlink(missing_ok=True)
    DAEMON_ERROR_FILE.unlink(missing_ok=True)
