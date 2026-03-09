"""Tests for CLI argument parsing — --online flag → internal provider names."""

from minidic.main import parse_args


class TestProviderMapping:
    def test_default_maps_to_parakeet(self):
        args = parse_args(["console"])
        assert args.provider == "parakeet"
        assert args.online is False

    def test_online_maps_to_whisper(self):
        args = parse_args(["console", "--online"])
        assert args.provider == "whisper"
        assert args.online is True

    def test_transcribe_default_maps_to_parakeet(self):
        args = parse_args(["transcribe", "file.wav"])
        assert args.provider == "parakeet"
        assert args.online is False

    def test_transcribe_online_maps_to_whisper(self):
        args = parse_args(["transcribe", "--online", "file.wav"])
        assert args.provider == "whisper"
        assert args.online is True
