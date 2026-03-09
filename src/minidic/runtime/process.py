"""Runtime process helpers for minidic services."""

from __future__ import annotations

import argparse
import fcntl
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TextIO

_MINIDIC_DIR = Path.home() / ".minidic"
_STATE_DIR = Path.home() / ".local" / "state" / "minidic"

DAEMON_PID_FILE = _STATE_DIR / "daemon.pid"
MENUBAR_PID_FILE = _STATE_DIR / "menubar.pid"
MENUBAR_LOCK_FILE = _STATE_DIR / "menubar.lock"
DAEMON_LOG_FILE = _STATE_DIR / "daemon.log"
MENUBAR_LOG_FILE = _STATE_DIR / "menubar.log"


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


def acquire_menubar_lock() -> TextIO | None:
    ensure_runtime_dirs()
    lock_file = MENUBAR_LOCK_FILE.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return None
    return lock_file


def write_menubar_lock_metadata(lock_file: TextIO, pid: int) -> None:
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(f"{pid}\n")
    lock_file.flush()
    os.fsync(lock_file.fileno())


def build_minidic_command(args: argparse.Namespace, subcommand: str) -> list[str]:
    cmd = [sys.executable, "-m", "minidic", subcommand]
    if args.verbose:
        cmd.append("--verbose")

    cmd.extend(["--provider", args.provider])
    if args.polish:
        cmd.append("--polish")
    cmd.extend(["--duration", str(args.duration)])
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
