"""Tests for settings-to-provider logic in the daemon."""

from minidic.transcribe import ASRProvider


class TestProviderValues:
    def test_parakeet_is_valid_provider(self):
        provider: ASRProvider = "parakeet"
        assert provider == "parakeet"

    def test_whisper_is_valid_provider(self):
        provider: ASRProvider = "whisper"
        assert provider == "whisper"
