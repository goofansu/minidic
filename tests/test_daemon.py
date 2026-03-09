"""Tests for the settings-to-provider conversion in the daemon."""

from minidic.daemon import _asr_to_provider


class TestAsrToProvider:
    def test_groq_setting_maps_to_whisper_provider(self):
        assert _asr_to_provider("groq") == "whisper"

    def test_offline_setting_maps_to_parakeet_provider(self):
        assert _asr_to_provider("offline") == "parakeet"

    def test_unknown_setting_maps_to_parakeet_provider(self):
        assert _asr_to_provider("anything") == "parakeet"
