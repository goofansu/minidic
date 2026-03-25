"""Tests for daemon hotkey listener coordination."""

from minidic import daemon as daemon_module
from minidic.transcribe import ASRProvider


class TestProviderValues:
    def test_parakeet_is_valid_provider(self):
        provider: ASRProvider = "parakeet"
        assert provider == "parakeet"

    def test_whisper_is_valid_provider(self):
        provider: ASRProvider = "whisper"
        assert provider == "whisper"


class TestHotkeyListenerBinding:
    def test_listener_kwargs_match_hotkey_mode(self):
        assert daemon_module._hotkey_listener_kwargs("toggle") == {
            "press_debounce_seconds": 0.3,
            "modifier_press_on_release": True,
        }
        assert daemon_module._hotkey_listener_kwargs("push_to_talk") == {
            "press_debounce_seconds": 0.05,
            "modifier_press_on_release": False,
        }

    def test_reload_if_needed_rebuilds_listener_when_mode_changes(self, monkeypatch):
        created = []

        class FakeListener:
            def __init__(
                self,
                *,
                on_press,
                on_release,
                hotkey,
                press_debounce_seconds,
                modifier_press_on_release,
            ) -> None:
                self.on_press = on_press
                self.on_release = on_release
                self.hotkey = hotkey
                self.press_debounce_seconds = press_debounce_seconds
                self.modifier_press_on_release = modifier_press_on_release
                self.started = False
                self.stopped = False
                created.append(self)

            def start(self) -> None:
                self.started = True

            def stop(self) -> None:
                self.stopped = True

        binding = daemon_module._HotkeyListenerBinding(
            on_press=lambda: None,
            on_release=lambda: None,
            listener_factory=FakeListener,
        )
        binding.start(hotkey="RIGHT_COMMAND", hotkey_mode="toggle")

        monkeypatch.setattr(daemon_module, "get_hotkey", lambda: "RIGHT_COMMAND")
        monkeypatch.setattr(daemon_module, "get_hotkey_mode", lambda: "push_to_talk")

        assert binding.reload_if_needed() is True
        assert len(created) == 2

        original_listener, reloaded_listener = created
        assert original_listener.started is True
        assert original_listener.stopped is True
        assert reloaded_listener.started is True
        assert reloaded_listener.stopped is False
        assert reloaded_listener.press_debounce_seconds == 0.05
        assert reloaded_listener.modifier_press_on_release is False
        assert binding.get_hotkey_mode() == "push_to_talk"

    def test_reload_if_needed_is_noop_when_settings_match(self, monkeypatch):
        created = []

        class FakeListener:
            def __init__(
                self,
                *,
                on_press,
                on_release,
                hotkey,
                press_debounce_seconds,
                modifier_press_on_release,
            ) -> None:
                self.hotkey = hotkey
                self.press_debounce_seconds = press_debounce_seconds
                self.modifier_press_on_release = modifier_press_on_release
                self.started = False
                self.stopped = False
                created.append(self)

            def start(self) -> None:
                self.started = True

            def stop(self) -> None:
                self.stopped = True

        binding = daemon_module._HotkeyListenerBinding(
            on_press=lambda: None,
            on_release=lambda: None,
            listener_factory=FakeListener,
        )
        binding.start(hotkey="RIGHT_COMMAND", hotkey_mode="toggle")

        monkeypatch.setattr(daemon_module, "get_hotkey", lambda: "RIGHT_COMMAND")
        monkeypatch.setattr(daemon_module, "get_hotkey_mode", lambda: "toggle")

        assert binding.reload_if_needed() is False
        assert len(created) == 1
        assert created[0].stopped is False
