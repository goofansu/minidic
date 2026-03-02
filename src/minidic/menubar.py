"""Menu bar app for minidic daemon status/control on macOS."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from AppKit import (
    NSAnimationContext,
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSColor,
    NSFont,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSScreen,
    NSStatusBar,
    NSStatusWindowLevel,
    NSTextAlignmentCenter,
    NSTextField,
    NSVariableStatusItemLength,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskBorderless,
)
from Foundation import NSMakeRect, NSObject, NSTimer

_MINIDIC_DIR = Path.home() / ".minidic"
_STATE_DIR = Path.home() / ".local" / "state" / "minidic"
_PID_FILE = _STATE_DIR / "daemon.pid"
_MENUBAR_PID_FILE = _STATE_DIR / "menubar.pid"
_LOG_FILE = _MINIDIC_DIR / "daemon.log"
_STATE_FILE = _STATE_DIR / "daemon.state"


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


def _read_runtime_state() -> str:
    try:
        state = _STATE_FILE.read_text().strip().lower()
    except OSError:
        return "idle"
    return state if state in {"idle", "recording", "transcribing"} else "idle"


def _infer_daemon_state() -> tuple[str, int | None, str]:
    """Return (state, pid, detail)."""
    pid = _read_pid()
    if pid is None:
        _STATE_FILE.unlink(missing_ok=True)
        return "stopped", None, "Daemon is not running"
    return "running", pid, "Running"


def _emoji_for_state(state: str) -> str:
    if state == "stopped":
        return "🛑"
    return "🎙️"


def _symbol_name_for_state(state: str) -> str:
    if state == "stopped":
        return "mic.slash"
    return "mic.fill"


def _set_menu_bar_icon(button: object, state: str) -> None:
    symbol_api = getattr(NSImage, "imageWithSystemSymbolName_accessibilityDescription_", None)
    if callable(symbol_api):
        image = symbol_api(_symbol_name_for_state(state), "minidic")
        if image is not None:
            image.setTemplate_(True)
            button.setImage_(image)
            button.setTitle_("")
            return

    button.setImage_(None)
    button.setTitle_(_emoji_for_state(state))


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

        self.last_runtime_state = "stopped"
        self.overlay_window = None
        self.overlay_timer = None

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
            0.2,
            self,
            "refreshStatus:",
            None,
            True,
        )

    def applicationWillTerminate_(self, notification: object) -> None:
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None
        if self.overlay_timer is not None:
            self.overlay_timer.invalidate()
            self.overlay_timer = None
        if self.overlay_window is not None:
            self.overlay_window.orderOut_(None)
            self.overlay_window = None
        _MENUBAR_PID_FILE.unlink(missing_ok=True)

    def refreshStatus_(self, timer: object) -> None:
        state, pid, detail = _infer_daemon_state()

        button = self.status_item.button()
        if button is not None:
            _set_menu_bar_icon(button, state)
            button.setToolTip_(f"minidic: {detail}")

        if pid is None:
            self.status_label_item.setTitle_("Status: stopped")
            self.toggle_daemon_item.setTitle_("Start daemon")
            runtime_state = "stopped"
        else:
            self.status_label_item.setTitle_(f"Status: {detail} (pid {pid})")
            self.toggle_daemon_item.setTitle_("Stop daemon")
            runtime_state = _read_runtime_state()

        if runtime_state == "recording" and self.last_runtime_state != "recording":
            self.showDictationOverlay_("🎙️ Dictation started")
        elif self.last_runtime_state == "recording" and runtime_state != "recording":
            self.showDictationOverlay_("Dictation stopped")

        self.last_runtime_state = runtime_state

    def showDictationOverlay_(self, message: str) -> None:
        if self.overlay_timer is not None:
            self.overlay_timer.invalidate()
            self.overlay_timer = None

        width = 200
        height = 44

        target_screen = NSScreen.mainScreen()
        if target_screen is None:
            return

        frame = target_screen.frame()
        top_margin = 48
        x = frame.origin.x + (frame.size.width - width) / 2
        y = frame.origin.y + frame.size.height - height - top_margin

        if self.overlay_window is None:
            self.overlay_window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                ((x, y), (width, height)),
                NSWindowStyleMaskBorderless,
                NSBackingStoreBuffered,
                False,
            )
            self.overlay_window.setLevel_(NSStatusWindowLevel)
            self.overlay_window.setOpaque_(False)
            self.overlay_window.setBackgroundColor_(NSColor.clearColor())
            self.overlay_window.setIgnoresMouseEvents_(True)
            self.overlay_window.setHasShadow_(True)
            self.overlay_window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary
            )

            # Dark rounded HUD
            content = self.overlay_window.contentView()
            content.setWantsLayer_(True)
            content.layer().setCornerRadius_(10.0)
            content.layer().setMasksToBounds_(True)
            content.layer().setBackgroundColor_(
                NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.88).CGColor()
            )

            font = NSFont.systemFontOfSize_weight_(13.0, 0.3)
            label_h = 20
            label_y = (height - label_h) / 2
            label = NSTextField.alloc().initWithFrame_(
                NSMakeRect(12, label_y, width - 24, label_h)
            )
            label.setAlignment_(NSTextAlignmentCenter)
            label.setTextColor_(NSColor.whiteColor())
            label.setFont_(font)
            label.setDrawsBackground_(False)
            label.setBezeled_(False)
            label.setEditable_(False)
            label.setSelectable_(False)
            label.setStringValue_(message)
            content.addSubview_(label)
        else:
            self.overlay_window.setFrame_display_(((x, y), (width, height)), True)
            label_h = 20
            label_y = (height - label_h) / 2
            for view in self.overlay_window.contentView().subviews():
                if isinstance(view, NSTextField):
                    view.setFrame_(NSMakeRect(12, label_y, width - 24, label_h))
                    view.setStringValue_(message)

        self.overlay_window.setAlphaValue_(0.0)
        self.overlay_window.orderFrontRegardless()

        def _fade_in(context: object) -> None:
            context.setDuration_(0.35)
            self.overlay_window.animator().setAlphaValue_(1.0)

        NSAnimationContext.runAnimationGroup_completionHandler_(_fade_in, None)

        self.overlay_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.2,
            self,
            "hideOverlay:",
            None,
            False,
        )

    def hideOverlay_(self, timer: object) -> None:
        if self.overlay_window is None:
            self.overlay_timer = None
            return

        def _fade_out(context: object) -> None:
            context.setDuration_(0.3)
            self.overlay_window.animator().setAlphaValue_(0.0)

        def _finish() -> None:
            if self.overlay_window is not None:
                self.overlay_window.orderOut_(None)

        NSAnimationContext.runAnimationGroup_completionHandler_(_fade_out, _finish)
        self.overlay_timer = None

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
