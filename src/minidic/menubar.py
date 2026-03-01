"""Menu bar app for minidic daemon status/control on macOS."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from AppKit import (
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSMenu,
    NSMenuItem,
    NSStatusBar,
    NSVariableStatusItemLength,
)
from Foundation import NSObject, NSTimer

_MINIDIC_DIR = Path.home() / ".minidic"
_PID_FILE = _MINIDIC_DIR / "daemon.pid"
_LOG_FILE = _MINIDIC_DIR / "daemon.log"


def _is_minidic_process(pid: int) -> bool:
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
        return "-m minidic" in out and "_daemon" in out
    except (OSError, subprocess.CalledProcessError):
        return False


def _read_pid() -> int | None:
    if not _PID_FILE.exists():
        return None
    try:
        pid = int(_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None
    if not _is_minidic_process(pid):
        _PID_FILE.unlink(missing_ok=True)
        return None
    return pid


def _tail_lines(path: Path, *, max_lines: int = 200, max_bytes: int = 24_000) -> list[str]:
    if not path.exists():
        return []

    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        read_size = min(size, max_bytes)
        if read_size == 0:
            return []
        f.seek(-read_size, os.SEEK_END)
        data = f.read(read_size)

    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return lines


def _infer_daemon_state() -> tuple[str, int | None, str]:
    """Return (state, pid, detail)."""
    pid = _read_pid()
    if pid is None:
        return "stopped", None, "Daemon is not running"

    lines = _tail_lines(_LOG_FILE)
    for line in reversed(lines):
        if "Recording started" in line:
            return "recording", pid, "Recording"
        if "Transcribing" in line or "Recording stopped" in line:
            return "transcribing", pid, "Transcribing"
        if "Injected:" in line or "No speech detected" in line:
            return "idle", pid, "Ready"
        if "Daemon ready" in line:
            return "idle", pid, "Ready"
        if "ERROR" in line or "crashed" in line:
            return "error", pid, "Error (check log)"

    return "idle", pid, "Running"


class MiniDicMenuBarApp(NSObject):
    def initWithArgs_(self, args: argparse.Namespace):
        self = self.init()
        if self is None:
            return None

        self.args = args

        self.status_item = None
        self.menu = None
        self.timer = None

        self.status_label_item = None
        self.toggle_daemon_item = None

        return self

    def applicationDidFinishLaunching_(self, notification: object) -> None:
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self.menu = NSMenu.alloc().init()

        self.status_label_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "minidic", None, ""
        )
        self.status_label_item.setEnabled_(False)
        self.menu.addItem_(self.status_label_item)

        self.menu.addItem_(NSMenuItem.separatorItem())

        self.toggle_daemon_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Start daemon", "toggleDaemon:", ""
        )
        self.toggle_daemon_item.setTarget_(self)
        self.menu.addItem_(self.toggle_daemon_item)

        open_log_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Open log", "openLog:", ""
        )
        open_log_item.setTarget_(self)
        self.menu.addItem_(open_log_item)

        self.menu.addItem_(NSMenuItem.separatorItem())

        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit", "quitApp:", "q"
        )
        quit_item.setTarget_(self)
        self.menu.addItem_(quit_item)

        self.status_item.setMenu_(self.menu)
        self.refreshStatus_(None)

        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0,
            self,
            "refreshStatus:",
            None,
            True,
        )

    def applicationWillTerminate_(self, notification: object) -> None:
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None

    def refreshStatus_(self, timer: object) -> None:
        state, pid, detail = _infer_daemon_state()

        if state == "stopped":
            title = "🛑"
        elif state == "recording":
            title = "🔴"
        elif state == "transcribing":
            title = "⏳"
        elif state == "error":
            title = "⚠️"
        else:
            title = "🎙️"

        button = self.status_item.button()
        if button is not None:
            button.setTitle_(title)
            button.setToolTip_(f"minidic: {detail}")

        if pid is None:
            self.status_label_item.setTitle_("Status: stopped")
            self.toggle_daemon_item.setTitle_("Start daemon")
        else:
            self.status_label_item.setTitle_(f"Status: {detail} (pid {pid})")
            self.toggle_daemon_item.setTitle_("Stop daemon")

    def buildCommandForSubcommand_(self, subcommand: str) -> list[str]:
        cmd = [sys.executable, "-m", "minidic"]
        if self.args.verbose:
            cmd.append("--verbose")
        cmd.extend(
            [
                "--model",
                self.args.model,
                "--duration",
                str(self.args.duration),
            ]
        )
        cmd.append(subcommand)
        return cmd

    def toggleDaemon_(self, sender: object) -> None:
        subcommand = "stop" if _read_pid() is not None else "start"
        devnull = open(os.devnull, "r+b")
        subprocess.Popen(
            self.buildCommandForSubcommand_(subcommand),
            stdin=devnull,
            stdout=devnull,
            stderr=devnull,
            start_new_session=True,
        )
        self.refreshStatus_(None)

    def openLog_(self, sender: object) -> None:
        _MINIDIC_DIR.mkdir(parents=True, exist_ok=True)
        _LOG_FILE.touch(exist_ok=True)
        subprocess.run(["open", str(_LOG_FILE)], check=False)

    def quitApp_(self, sender: object) -> None:
        NSApp.terminate_(None)


def run_menubar(args: argparse.Namespace) -> None:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    delegate = MiniDicMenuBarApp.alloc().initWithArgs_(args)
    app.setDelegate_(delegate)
    app.run()
