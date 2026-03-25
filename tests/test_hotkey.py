"""Tests for hotkey parsing helpers."""

from minidic import hotkey as hotkey_module
from minidic.hotkey import (
    GlobalHotkeyListener,
    MODIFIER_DEVICE_FLAG,
    kCGEventFlagsChanged,
    kCGEventKeyDown,
    kCGEventLeftMouseDown,
    normalize_hotkey,
    parse_hotkey_keycode,
)


class TestHotkeyHelpers:
    def test_parse_hotkey_keycode_accepts_modifier_names_case_insensitively(self):
        assert parse_hotkey_keycode("RIGHT_COMMAND") == 54
        assert parse_hotkey_keycode("right_command") == 54

    def test_parse_hotkey_keycode_rejects_unsupported_keys(self):
        for hotkey in ("LEFT_COMMAND", "FN", "SPACE"):
            try:
                parse_hotkey_keycode(hotkey)
            except ValueError:
                pass
            else:
                raise AssertionError(f"Expected ValueError for {hotkey}")

    def test_normalize_hotkey_uppercases_modifier_names(self):
        assert normalize_hotkey("right_command") == "RIGHT_COMMAND"

    def test_modifier_hotkey_does_not_fire_when_used_in_combo(self):
        events: list[str] = []
        listener = GlobalHotkeyListener(
            on_press=lambda: events.append("press"),
            on_release=lambda: events.append("release"),
            hotkey="RIGHT_COMMAND",
            press_debounce_seconds=0,
        )

        listener._pressed = True
        listener._press_fired = False
        listener._modifier_combo_active = False
        listener._note_modifier_combo(event_type=kCGEventKeyDown)
        listener._fire_modifier_press()

        assert events == []

    def test_modifier_combo_cancels_already_fired_hotkey(self):
        events: list[str] = []
        listener = GlobalHotkeyListener(
            on_press=lambda: events.append("press"),
            on_release=lambda: events.append("release"),
            hotkey="RIGHT_COMMAND",
            press_debounce_seconds=0,
        )

        listener._pressed = True
        listener._fire_modifier_press()
        listener._note_modifier_combo(event_type=kCGEventKeyDown)

        assert events == ["press", "release"]

    def test_modifier_hotkey_tap_fires_on_release_when_configured(self):
        events: list[str] = []
        listener = GlobalHotkeyListener(
            on_press=lambda: events.append("press"),
            on_release=lambda: events.append("release"),
            hotkey="RIGHT_COMMAND",
            press_debounce_seconds=0.3,
            modifier_press_on_release=True,
        )

        listener._handle_modifier_press()
        listener._handle_modifier_release()

        assert events == ["press"]

    def test_modifier_hotkey_flags_changed_event_is_swallowed(self, monkeypatch):
        events: list[str] = []
        listener = GlobalHotkeyListener(
            on_press=lambda: events.append("press"),
            on_release=lambda: events.append("release"),
            hotkey="RIGHT_COMMAND",
            press_debounce_seconds=0,
        )

        flags = iter((MODIFIER_DEVICE_FLAG[listener._hotkey_keycode], 0))
        monkeypatch.setattr(hotkey_module, "CGEventGetFlags", lambda event: next(flags))

        assert listener._handle_modifier_event(kCGEventFlagsChanged, object()) is None
        assert listener._handle_modifier_event(kCGEventFlagsChanged, object()) is None

        assert events == ["press", "release"]

    def test_modifier_hotkey_combo_is_ignored_when_press_fires_on_release(self):
        events: list[str] = []
        listener = GlobalHotkeyListener(
            on_press=lambda: events.append("press"),
            on_release=lambda: events.append("release"),
            hotkey="RIGHT_COMMAND",
            press_debounce_seconds=0.3,
            modifier_press_on_release=True,
        )

        listener._handle_modifier_press()
        listener._note_modifier_combo(event_type=kCGEventKeyDown)
        listener._handle_modifier_release()

        assert events == []

    def test_modifier_mouse_combo_is_ignored_when_press_fires_on_release(self, monkeypatch):
        events: list[str] = []
        listener = GlobalHotkeyListener(
            on_press=lambda: events.append("press"),
            on_release=lambda: events.append("release"),
            hotkey="RIGHT_COMMAND",
            press_debounce_seconds=0.3,
            modifier_press_on_release=True,
        )

        listener._handle_modifier_press()
        monkeypatch.setattr(
            hotkey_module,
            "CGEventGetIntegerValueField",
            lambda event, field: (_ for _ in ()).throw(AssertionError("unexpected keycode lookup")),
        )

        mouse_event = object()
        assert listener._callback(None, kCGEventLeftMouseDown, mouse_event, None) is mouse_event
        listener._handle_modifier_release()

        assert events == []

    def test_modifier_release_cancels_pending_press_even_if_timer_fires_late(self, monkeypatch):
        events: list[str] = []
        timers: list[FakeTimer] = []

        class FakeTimer:
            def __init__(self, interval: float, callback):
                self.interval = interval
                self.callback = callback
                self.daemon = False
                self.cancelled = False
                timers.append(self)

            def start(self) -> None:
                return

            def cancel(self) -> None:
                self.cancelled = True

            def fire(self) -> None:
                self.callback()

        monkeypatch.setattr(hotkey_module.threading, "Timer", FakeTimer)

        listener = GlobalHotkeyListener(
            on_press=lambda: events.append("press"),
            on_release=lambda: events.append("release"),
            hotkey="RIGHT_COMMAND",
            press_debounce_seconds=0.3,
        )

        listener._handle_modifier_press()
        assert len(timers) == 1

        timer = timers[0]
        listener._handle_modifier_release()

        assert timer.cancelled is True
        timer.fire()

        assert events == []

    def test_stop_cancels_pending_modifier_press_timer(self, monkeypatch):
        events: list[str] = []
        timers: list[FakeTimer] = []

        class FakeTimer:
            def __init__(self, interval: float, callback):
                self.interval = interval
                self.callback = callback
                self.daemon = False
                self.cancelled = False
                timers.append(self)

            def start(self) -> None:
                return

            def cancel(self) -> None:
                self.cancelled = True

            def fire(self) -> None:
                self.callback()

        monkeypatch.setattr(hotkey_module.threading, "Timer", FakeTimer)

        listener = GlobalHotkeyListener(
            on_press=lambda: events.append("press"),
            on_release=lambda: events.append("release"),
            hotkey="RIGHT_COMMAND",
            press_debounce_seconds=0.3,
        )

        listener._handle_modifier_press()
        assert len(timers) == 1

        timer = timers[0]
        assert listener._press_timer is timer

        listener.stop()

        assert timer.cancelled is True
        assert listener._press_timer is None

        timer.fire()

        assert events == []

    def test_event_tap_mask_includes_modifier_mouse_events(self, monkeypatch):
        event_ids = {
            "kCGEventKeyDown": 10,
            "kCGEventKeyUp": 11,
            "kCGEventFlagsChanged": 12,
            "kCGEventLeftMouseDown": 1,
            "kCGEventLeftMouseUp": 2,
            "kCGEventLeftMouseDragged": 3,
            "kCGEventRightMouseDown": 4,
            "kCGEventRightMouseUp": 5,
            "kCGEventRightMouseDragged": 6,
            "kCGEventOtherMouseDown": 7,
            "kCGEventOtherMouseUp": 8,
            "kCGEventOtherMouseDragged": 9,
            "kCGEventScrollWheel": 13,
        }
        for name, value in event_ids.items():
            monkeypatch.setattr(hotkey_module, name, value)

        monkeypatch.setattr(
            hotkey_module,
            "EVENT_TAP_EVENT_TYPES",
            set(event_ids.values()),
        )

        captured = {}
        monkeypatch.setattr(
            hotkey_module,
            "CGEventTapCreate",
            lambda tap, place, options, mask, callback, refcon: captured.setdefault("mask", mask) or object(),
        )
        monkeypatch.setattr(hotkey_module, "CFMachPortCreateRunLoopSource", lambda *args: object())
        monkeypatch.setattr(hotkey_module, "CFRunLoopGetCurrent", lambda: object())
        monkeypatch.setattr(hotkey_module, "CFRunLoopAddSource", lambda *args: None)
        monkeypatch.setattr(hotkey_module, "CGEventTapEnable", lambda *args: None)
        monkeypatch.setattr(hotkey_module, "CFRunLoopRun", lambda: None)

        listener = GlobalHotkeyListener(
            on_press=lambda: None,
            on_release=lambda: None,
            hotkey="RIGHT_COMMAND",
        )
        listener._run()

        expected_mask = 0
        for event_type in event_ids.values():
            expected_mask |= 1 << event_type

        assert captured["mask"] == expected_mask
