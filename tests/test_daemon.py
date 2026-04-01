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
    def test_reload_if_needed_rebuilds_listener_when_hotkey_changes(self, monkeypatch):
        created = []

        class FakeListener:
            def __init__(self, *, on_hotkey, hotkey) -> None:
                self.on_hotkey = on_hotkey
                self.hotkey = hotkey
                self.started = False
                self.stopped = False
                created.append(self)

            def start(self) -> None:
                self.started = True

            def stop(self) -> None:
                self.stopped = True

        binding = daemon_module._HotkeyListenerBinding(
            on_hotkey=lambda: None,
            listener_factory=FakeListener,
        )
        binding.start(hotkey="F5")

        monkeypatch.setattr(daemon_module, "get_hotkey", lambda: "F6")

        assert binding.reload_if_needed() is True
        assert len(created) == 2

        original_listener, reloaded_listener = created
        assert original_listener.started is True
        assert original_listener.stopped is True
        assert reloaded_listener.started is True
        assert reloaded_listener.stopped is False
        assert reloaded_listener.hotkey == "F6"

    def test_reload_if_needed_is_noop_when_settings_match(self, monkeypatch):
        created = []

        class FakeListener:
            def __init__(self, *, on_hotkey, hotkey) -> None:
                self.hotkey = hotkey
                self.started = False
                self.stopped = False
                created.append(self)

            def start(self) -> None:
                self.started = True

            def stop(self) -> None:
                self.stopped = True

        binding = daemon_module._HotkeyListenerBinding(
            on_hotkey=lambda: None,
            listener_factory=FakeListener,
        )
        binding.start(hotkey="F5")

        monkeypatch.setattr(daemon_module, "get_hotkey", lambda: "F5")

        assert binding.reload_if_needed() is False
        assert len(created) == 1
        assert created[0].stopped is False
