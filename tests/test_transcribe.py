"""Tests for ASR provider validation and Transcriber backend selection."""

import pytest

from minidic.transcribe import (
    DEFAULT_MODEL,
    GROQ_DEFAULT_MODEL,
    _GroqTranscriber,
    _LocalTranscriber,
    Transcriber,
    validate_transcriber_settings,
)


class TestValidateTranscriberSettings:
    def test_parakeet_is_accepted(self):
        validate_transcriber_settings(provider="parakeet", polish=False)

    def test_whisper_is_accepted(self):
        validate_transcriber_settings(provider="whisper", polish=False)

    def test_groq_is_rejected(self):
        with pytest.raises(ValueError, match="Unsupported ASR provider"):
            validate_transcriber_settings(provider="groq", polish=False)  # type: ignore[arg-type]

    def test_unknown_provider_is_rejected(self):
        with pytest.raises(ValueError, match="Unsupported ASR provider"):
            validate_transcriber_settings(provider="unknown", polish=False)  # type: ignore[arg-type]


class TestTranscriberModelId:
    def test_parakeet_uses_local_model(self):
        t = Transcriber("parakeet")
        assert t.model_id == DEFAULT_MODEL

    def test_whisper_uses_groq_model(self):
        t = Transcriber("whisper")
        assert t.model_id == GROQ_DEFAULT_MODEL


class TestTranscriberBackend:
    def test_parakeet_uses_local_backend(self):
        t = Transcriber("parakeet")
        assert isinstance(t._backend, _LocalTranscriber)

    def test_whisper_uses_groq_backend(self):
        t = Transcriber("whisper")
        assert isinstance(t._backend, _GroqTranscriber)
