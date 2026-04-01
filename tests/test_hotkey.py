"""Tests for hotkey parsing helpers."""

from minidic import hotkey as hotkey_module
from minidic.hotkey import (
    GlobalHotkeyListener,
    kCGEventKeyDown,
    normalize_hotkey,
    parse_hotkey_keycode,
)


class TestHotkeyHelpers:
    def test_parse_hotkey_keycode_accepts_function_keys_case_insensitively(self):
        assert parse_hotkey_keycode("F5") == 96
        assert parse_hotkey_keycode("f5") == 96

    def test_parse_hotkey_keycode_rejects_unsupported_keys(self):
        for hotkey in ("RIGHT_COMMAND", "LEFT_COMMAND", "FN", "SPACE"):
            try:
                parse_hotkey_keycode(hotkey)
            except ValueError:
                pass
            else:
                raise AssertionError(f"Expected ValueError for {hotkey}")

    def test_normalize_hotkey_uppercases(self):
        assert normalize_hotkey("f5") == "F5"

    def test_event_tap_mask_covers_key_down_only(self, monkeypatch):
        event_ids = {"kCGEventKeyDown": 10}
        for name, value in event_ids.items():
            monkeypatch.setattr(hotkey_module, name, value)

        monkeypatch.setattr(hotkey_module, "EVENT_TAP_EVENT_TYPES", set(event_ids.values()))

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

        listener = GlobalHotkeyListener(on_hotkey=lambda: None, hotkey="F5")
        listener._run()

        assert captured["mask"] == 1 << 10
