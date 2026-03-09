"""Tests for CLI argument parsing — user-facing ASR names → internal provider names."""

from minidic.main import parse_args


class TestParseArgsProviderMapping:
    def test_offline_maps_to_parakeet(self):
        args = parse_args(["console", "--asr", "offline"])
        assert args.provider == "parakeet"

    def test_groq_maps_to_whisper(self):
        args = parse_args(["console", "--asr", "groq"])
        assert args.provider == "whisper"

    def test_default_maps_to_parakeet(self):
        args = parse_args(["console"])
        assert args.provider == "parakeet"

    def test_transcribe_offline_maps_to_parakeet(self):
        args = parse_args(["transcribe", "--asr", "offline", "file.wav"])
        assert args.provider == "parakeet"

    def test_transcribe_groq_maps_to_whisper(self):
        args = parse_args(["transcribe", "--asr", "groq", "file.wav"])
        assert args.provider == "whisper"
