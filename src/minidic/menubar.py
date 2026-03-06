"""Menu bar UI for minidic daemon status/control on macOS."""

from __future__ import annotations

import argparse
import subprocess
import time

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

from minidic.runtime.process import (
    DAEMON_LOG_FILE,
    DAEMON_PID_FILE,
    build_minidic_command,
    ensure_runtime_dirs,
    read_daemon_pid,
    spawn_detached,
    stop_pid,
)
from minidic.runtime.state import read_runtime_state
from minidic.settings import (
    get_gemini_enabled,
    get_recording_duration,
    set_gemini_enabled,
    set_recording_duration,
)


def _infer_daemon_state() -> tuple[str, int | None, str]:
    pid = read_daemon_pid()
    if pid is None:
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


DURATION_PRESETS = (15.0, 30.0, 60.0, 90.0, 120.0)


def _format_duration(duration: float) -> str:
    if duration.is_integer():
        return f"{int(duration)}s"
    return f"{duration:g}s"


class MiniDicMenuBarApp(NSObject):
    def initWithArgs_(self, args: argparse.Namespace):
        self = self.init()
        if self is None:
            return None

        self.args = args
        self.args.duration = get_recording_duration(default=args.duration)

        self.status_item = None
        self.menu = None
        self.timer = None

        self.status_label_item = None
        self.toggle_daemon_item = None
        self.duration_menu_item = None
        self.duration_items: dict[float, object] = {}
        self.toggle_gemini_item = None

        self.last_runtime_state = "stopped"
        self.overlay_window = None
        self.overlay_timer = None
        self.transcribing_overlay_visible = False

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

        duration_menu = NSMenu.alloc().init()
        self.duration_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Duration", None, ""
        )
        self.menu.addItem_(self.duration_menu_item)
        self.menu.setSubmenu_forItem_(duration_menu, self.duration_menu_item)

        for duration in DURATION_PRESETS:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                _format_duration(duration), "selectDuration:", ""
            )
            item.setTarget_(self)
            item.setTag_(int(duration))
            duration_menu.addItem_(item)
            self.duration_items[duration] = item

        self.toggle_gemini_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Gemini mode", "toggleGemini:", ""
        )
        self.toggle_gemini_item.setTarget_(self)
        self.menu.addItem_(self.toggle_gemini_item)

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
            0.5,
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
            runtime_state = read_runtime_state()

        current_duration = get_recording_duration(default=self.args.duration)
        self.args.duration = current_duration
        if self.duration_menu_item is not None:
            self.duration_menu_item.setTitle_(f"Duration: {_format_duration(current_duration)}")
        for duration, item in self.duration_items.items():
            item.setState_(1 if duration == current_duration else 0)

        gemini_enabled = get_gemini_enabled(default=self.args.gemini)
        if self.toggle_gemini_item is not None:
            self.toggle_gemini_item.setState_(1 if gemini_enabled else 0)

        if runtime_state == "transcribing":
            self.showTranscribingOverlay_(None)
        elif self.last_runtime_state == "transcribing" and runtime_state != "transcribing":
            self.hideTranscribingOverlay_(None)

        if runtime_state == "recording" and self.last_runtime_state != "recording":
            self.showDictationOverlay_("🎙️ Dictation started")
        elif (
            self.last_runtime_state == "recording"
            and runtime_state == "idle"
        ):
            self.showDictationOverlay_("Dictation stopped")

        self.last_runtime_state = runtime_state

    def showTranscribingOverlay_(self, sender: object) -> None:
        if self.transcribing_overlay_visible:
            return

        self.transcribing_overlay_visible = True
        self.showDictationOverlay_("⏳ Transcribing")

        # Keep overlay visible for the entire transcription phase.
        if self.overlay_timer is not None:
            self.overlay_timer.invalidate()
            self.overlay_timer = None

    def hideTranscribingOverlay_(self, sender: object) -> None:
        self.transcribing_overlay_visible = False
        self.hideOverlay_(None)

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

    def toggleDaemon_(self, sender: object) -> None:
        pid = read_daemon_pid()
        if pid is not None:
            if stop_pid(pid, timeout_seconds=5.0):
                DAEMON_PID_FILE.unlink(missing_ok=True)
            self.refreshStatus_(None)
            return

        cmd = build_minidic_command(self.args, "_daemon")
        spawn_detached(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if read_daemon_pid() is not None:
                break
            time.sleep(0.1)

        self.refreshStatus_(None)

    def selectDuration_(self, sender: object) -> None:
        duration = float(sender.tag())
        set_recording_duration(duration)
        self.args.duration = duration
        self.refreshStatus_(None)

    def toggleGemini_(self, sender: object) -> None:
        enabled = get_gemini_enabled(default=self.args.gemini)
        set_gemini_enabled(not enabled)
        self.refreshStatus_(None)

    def openLog_(self, sender: object) -> None:
        ensure_runtime_dirs()
        DAEMON_LOG_FILE.touch(exist_ok=True)
        subprocess.run(["open", str(DAEMON_LOG_FILE)], check=False)

    def quitApp_(self, sender: object) -> None:
        NSApp.terminate_(None)


def run_menubar(args: argparse.Namespace) -> None:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    delegate = MiniDicMenuBarApp.alloc().initWithArgs_(args)
    app.setDelegate_(delegate)
    app.run()
