"""Menu bar UI for minidic daemon status/control on macOS."""

from __future__ import annotations

import argparse
import os
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

from minidic._version import version_string
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
    get_asr_settings,
    get_enhancement_settings,
    get_recording_duration,
    set_asr_settings,
    set_enhancement_settings,
    set_recording_duration,
)

ASR_PROVIDER_TAGS = {0: "parakeet", 1: "groq"}
ENHANCEMENT_PROVIDER_TAGS = {0: "none", 1: "groq"}
DURATION_PRESETS = (15.0, 30.0, 60.0, 90.0, 120.0)


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


def _format_duration(duration: float) -> str:
    if duration.is_integer():
        return f"{int(duration)}s"
    return f"{duration:g}s"


def _groq_available() -> bool:
    return bool(os.environ.get("GROQ_API_KEY", "").strip())


def _asr_label(provider: str, *, available: bool | None = None) -> str:
    if provider == "groq":
        if available is False:
            return "Online (Groq) — requires GROQ_API_KEY"
        return "Online (Groq)"
    return "Offline (Parakeet)"


def _enhancement_label(provider: str, *, available: bool | None = None) -> str:
    if provider == "groq":
        if available is False:
            return "Groq — requires GROQ_API_KEY"
        return "Groq"
    return "None"


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

        self.toggle_daemon_item = None
        self.asr_menu_item = None
        self.asr_items: dict[str, object] = {}
        self.enhancement_menu_item = None
        self.enhancement_items: dict[str, object] = {}
        self.duration_menu_item = None
        self.duration_items: dict[float, object] = {}

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

        title_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"minidic {version_string()}", None, ""
        )
        title_item.setEnabled_(False)
        self.menu.addItem_(title_item)

        self.menu.addItem_(NSMenuItem.separatorItem())

        self.toggle_daemon_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Start daemon", "toggleDaemon:", ""
        )
        self.toggle_daemon_item.setTarget_(self)
        self.menu.addItem_(self.toggle_daemon_item)

        asr_menu = NSMenu.alloc().init()
        self.asr_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("ASR", None, "")
        self.menu.addItem_(self.asr_menu_item)
        self.menu.setSubmenu_forItem_(asr_menu, self.asr_menu_item)

        asr_parakeet_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            _asr_label("parakeet"), "selectAsrProvider:", ""
        )
        asr_parakeet_item.setTarget_(self)
        asr_parakeet_item.setTag_(0)
        asr_menu.addItem_(asr_parakeet_item)
        self.asr_items["parakeet"] = asr_parakeet_item

        asr_groq_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            _asr_label("groq", available=_groq_available()), "selectAsrProvider:", ""
        )
        asr_groq_item.setTarget_(self)
        asr_groq_item.setTag_(1)
        asr_menu.addItem_(asr_groq_item)
        self.asr_items["groq"] = asr_groq_item

        enhancement_menu = NSMenu.alloc().init()
        self.enhancement_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Enhancement", None, ""
        )
        self.menu.addItem_(self.enhancement_menu_item)
        self.menu.setSubmenu_forItem_(enhancement_menu, self.enhancement_menu_item)

        enhancement_none_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            _enhancement_label("none"), "selectEnhancementProvider:", ""
        )
        enhancement_none_item.setTarget_(self)
        enhancement_none_item.setTag_(0)
        enhancement_menu.addItem_(enhancement_none_item)
        self.enhancement_items["none"] = enhancement_none_item

        enhancement_groq_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            _enhancement_label("groq", available=_groq_available()),
            "selectEnhancementProvider:",
            "",
        )
        enhancement_groq_item.setTarget_(self)
        enhancement_groq_item.setTag_(1)
        enhancement_menu.addItem_(enhancement_groq_item)
        self.enhancement_items["groq"] = enhancement_groq_item

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
            self.toggle_daemon_item.setTitle_("Start daemon")
            runtime_state = "stopped"
        else:
            self.toggle_daemon_item.setTitle_("Stop daemon")
            runtime_state = read_runtime_state()

        asr_settings = get_asr_settings()
        enhancement_settings = get_enhancement_settings()
        current_duration = get_recording_duration(default=self.args.duration)

        self.args.provider = asr_settings["provider"]
        self.args.enhancement = enhancement_settings["provider"]
        self.args.duration = current_duration

        groq_available = _groq_available()

        if self.asr_menu_item is not None:
            self.asr_menu_item.setTitle_(f"ASR: {_asr_label(asr_settings['provider'])}")
        for provider, item in self.asr_items.items():
            item.setState_(1 if provider == asr_settings["provider"] else 0)
            if provider == "groq":
                item.setTitle_(_asr_label("groq", available=groq_available))
                item.setEnabled_(groq_available)

        if self.enhancement_menu_item is not None:
            self.enhancement_menu_item.setTitle_(
                f"Enhancement: {_enhancement_label(enhancement_settings['provider'])}"
            )
        for provider, item in self.enhancement_items.items():
            item.setState_(1 if provider == enhancement_settings["provider"] else 0)
            if provider == "groq":
                item.setTitle_(_enhancement_label("groq", available=groq_available))
                item.setEnabled_(groq_available)

        if self.duration_menu_item is not None:
            self.duration_menu_item.setTitle_(f"Duration: {_format_duration(current_duration)}")
        for duration, item in self.duration_items.items():
            item.setState_(1 if duration == current_duration else 0)

        if runtime_state == "transcribing":
            self.showTranscribingOverlay_(None)
        elif self.last_runtime_state == "transcribing" and runtime_state != "transcribing":
            self.hideTranscribingOverlay_(None)

        if runtime_state == "recording" and self.last_runtime_state != "recording":
            self.showDictationOverlay_("Listening")
        elif self.last_runtime_state == "recording" and runtime_state == "idle":
            self.hideOverlay_(None)

        self.last_runtime_state = runtime_state

    def showTranscribingOverlay_(self, sender: object) -> None:
        if self.transcribing_overlay_visible:
            return

        self.transcribing_overlay_visible = True
        self.showDictationOverlay_("Transcribing")

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

        persistent = message in {"Listening", "Transcribing"}

        width = 260
        height = 88

        if message == "Transcribing":
            icon_text = "⏳"
        else:
            icon_text = "🎙️"

        target_screen = NSScreen.mainScreen()
        if target_screen is None:
            return

        frame = target_screen.visibleFrame()
        top_offset = 140
        x = frame.origin.x + (frame.size.width - width) / 2
        y = frame.origin.y + frame.size.height - height - top_offset

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
            content.layer().setCornerRadius_(20.0)
            content.layer().setMasksToBounds_(True)
            content.layer().setBackgroundColor_(
                NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.92).CGColor()
            )
            content.layer().setBorderWidth_(1.0)
            content.layer().setBorderColor_(
                NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.12).CGColor()
            )

            badge = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 48, width, 24))
            badge.setTag_(1)
            badge.setAlignment_(NSTextAlignmentCenter)
            badge.setTextColor_(NSColor.whiteColor())
            badge.setFont_(NSFont.systemFontOfSize_weight_(22.0, 0.5))
            badge.setDrawsBackground_(False)
            badge.setBezeled_(False)
            badge.setEditable_(False)
            badge.setSelectable_(False)
            badge.setStringValue_(icon_text)
            content.addSubview_(badge)

            label = NSTextField.alloc().initWithFrame_(NSMakeRect(18, 20, width - 36, 22))
            label.setTag_(3)
            label.setAlignment_(NSTextAlignmentCenter)
            label.setTextColor_(NSColor.whiteColor())
            label.setFont_(NSFont.systemFontOfSize_weight_(15.0, 0.45))
            label.setDrawsBackground_(False)
            label.setBezeled_(False)
            label.setEditable_(False)
            label.setSelectable_(False)
            label.setStringValue_(message)
            content.addSubview_(label)
        else:
            self.overlay_window.setFrame_display_(((x, y), (width, height)), True)
            for view in self.overlay_window.contentView().subviews():
                if not isinstance(view, NSTextField):
                    continue
                if view.tag() == 1:
                    view.setFrame_(NSMakeRect(0, 48, width, 24))
                    view.setStringValue_(icon_text)
                elif view.tag() == 3:
                    view.setFrame_(NSMakeRect(18, 20, width - 36, 22))
                    view.setStringValue_(message)

        self.overlay_window.setAlphaValue_(0.0)
        self.overlay_window.orderFrontRegardless()

        def _fade_in(context: object) -> None:
            context.setDuration_(0.35)
            self.overlay_window.animator().setAlphaValue_(1.0)

        NSAnimationContext.runAnimationGroup_completionHandler_(_fade_in, None)

        if not persistent:
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

    def selectAsrProvider_(self, sender: object) -> None:
        provider = ASR_PROVIDER_TAGS.get(int(sender.tag()))
        if provider is None:
            return
        if provider == "groq" and not _groq_available():
            return
        set_asr_settings({"provider": provider})
        self.refreshStatus_(None)

    def selectEnhancementProvider_(self, sender: object) -> None:
        provider = ENHANCEMENT_PROVIDER_TAGS.get(int(sender.tag()))
        if provider is None:
            return
        if provider == "groq" and not _groq_available():
            return
        set_enhancement_settings({"provider": provider})
        self.refreshStatus_(None)

    def selectDuration_(self, sender: object) -> None:
        duration = float(sender.tag())
        set_recording_duration(duration)
        self.args.duration = duration
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
