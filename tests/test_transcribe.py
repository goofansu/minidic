"""Tests for ASR provider validation and Transcriber backend selection."""

import numpy as np
import pytest

from minidic.transcribe import (
    DEFAULT_MODEL,
    GROQ_DEFAULT_MODEL,
    _GroqTranscriber,
    _LocalTranscriber,
    _PolishConfig,
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

    def test_non_string_prompt_is_rejected(self):
        with pytest.raises(ValueError, match="Unsupported Groq Whisper prompt"):
            validate_transcriber_settings(provider="whisper", polish=False, prompt=123)  # type: ignore[arg-type]


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


class _FakeResponse:
    text = "hello world"


class _FakeTranscriptions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse()


class _FakeAudio:
    def __init__(self, transcriptions: _FakeTranscriptions) -> None:
        self.transcriptions = transcriptions


class _FakeClient:
    def __init__(self, transcriptions: _FakeTranscriptions) -> None:
        self.audio = _FakeAudio(transcriptions)


class TestGroqPrompt:
    def test_custom_prompt_is_sent(self):
        transcriptions = _FakeTranscriptions()
        backend = _GroqTranscriber(
            GROQ_DEFAULT_MODEL,
            config=_PolishConfig(enabled=False),
            prompt="Hello, world!",
        )
        backend._client = _FakeClient(transcriptions)

        text = backend.transcribe(np.zeros(160, dtype=np.float32))

        assert text == "hello world"
        assert transcriptions.calls[0]["prompt"] == "Hello, world!"

    def test_empty_prompt_is_omitted(self):
        transcriptions = _FakeTranscriptions()
        backend = _GroqTranscriber(
            GROQ_DEFAULT_MODEL,
            config=_PolishConfig(enabled=False),
            prompt="",
        )
        backend._client = _FakeClient(transcriptions)

        backend.transcribe(np.zeros(160, dtype=np.float32))

        assert "prompt" not in transcriptions.calls[0]
