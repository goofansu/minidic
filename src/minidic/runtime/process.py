"""Runtime process helpers for minidic services."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

_MINIDIC_DIR = Path.home() / ".minidic"
_STATE_DIR = Path.home() / ".local" / "state" / "minidic"

DAEMON_PID_FILE = _STATE_DIR / "daemon.pid"
MENUBAR_PID_FILE = _STATE_DIR / "menubar.pid"
DAEMON_LOG_FILE = _STATE_DIR / "daemon.log"
MENUBAR_LOG_FILE = _STATE_DIR / "menubar.log"
DAEMON_STATE_FILE = _STATE_DIR / "daemon.state"


def ensure_runtime_dirs() -> None:
    _MINIDIC_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_DIR.mkdir(parents=True, exist_ok=True)


def _is_subcommand_process(pid: int, subcommand: str) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False

    try:
        out = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return "-m minidic" in out and subcommand in out
    except (OSError, subprocess.CalledProcessError):
        return False


def _read_pid_file(pid_file: Path, *, subcommand: str) -> int | None:
    if not pid_file.exists():
        return None

    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return None

    if not _is_subcommand_process(pid, subcommand):
        pid_file.unlink(missing_ok=True)
        return None

    return pid


def read_daemon_pid() -> int | None:
    return _read_pid_file(DAEMON_PID_FILE, subcommand="_daemon")


def read_menubar_pid() -> int | None:
    return _read_pid_file(MENUBAR_PID_FILE, subcommand="_menubar")


def build_minidic_command(args: argparse.Namespace, subcommand: str) -> list[str]:
    cmd = [sys.executable, "-m", "minidic", subcommand]
    if args.verbose:
        cmd.append("--verbose")
    if args.gemini:
        cmd.append("--gemini")
    cmd.extend(["--model", args.model, "--duration", str(args.duration)])
    return cmd


def spawn_detached(
    cmd: list[str],
    *,
    stdout: object,
    stderr: object,
) -> subprocess.Popen:
    devnull = open(os.devnull, "r+b")
    return subprocess.Popen(
        cmd,
        stdin=devnull,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )


def stop_pid(pid: int, *, timeout_seconds: float = 5.0) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.1)

    return False


def read_runtime_state() -> str:
    try:
        state = DAEMON_STATE_FILE.read_text().strip().lower()
    except OSError:
        return "idle"
    return state if state in {"idle", "recording", "transcribing"} else "idle"


def write_runtime_state(state: str) -> None:
    DAEMON_STATE_FILE.write_text(state)


def clear_runtime_state() -> None:
    DAEMON_STATE_FILE.unlink(missing_ok=True)
