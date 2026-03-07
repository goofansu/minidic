"""Speech-to-text transcription backends for minidic."""

from __future__ import annotations

import gc
import io
import json
import logging
import os
import wave
from dataclasses import dataclass
from typing import Any, Literal

import mlx.core as mx
import numpy as np
import parakeet_mlx

from minidic.audio import TARGET_RATE
from minidic.text_processing import GeminiSmoother, RegexSmoother

logger = logging.getLogger(__name__)

ASRProvider = Literal["parakeet", "groq"]
EnhancementProvider = Literal["none", "gemini"]

DEFAULT_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"
GROQ_DEFAULT_MODEL = "whisper-large-v3-turbo"
CONTEXT_SIZE = (256, 256)
STREAM_DEPTH = 1


@dataclass(frozen=True)
class _EnhancementConfig:
    provider: EnhancementProvider


class _BaseTranscriber:
    def __init__(self, *, config: _EnhancementConfig, strip_fillers: bool = True) -> None:
        self.strip_fillers = strip_fillers
        self._regex_smoother = RegexSmoother() if strip_fillers else None
        self._enhancement_provider = config.provider
        self._smoother: GeminiSmoother | None = None
        if self._enhancement_provider == "gemini":
            self._smoother = GeminiSmoother()

    def load(self) -> None:
        raise NotImplementedError

    def unload(self) -> None:
        raise NotImplementedError

    def set_enhancement(self, provider: EnhancementProvider) -> None:
        if provider == self._enhancement_provider:
            return

        self._enhancement_provider = provider
        if provider == "gemini":
            self._smoother = GeminiSmoother()
        else:
            self._smoother = None

    def set_gemini_enabled(self, enabled: bool) -> None:
        self.set_enhancement("gemini" if enabled else "none")

    def _clean_text(self, text: str) -> str:
        cleaned = text.strip()
        if self._regex_smoother is not None:
            cleaned = self._regex_smoother.smooth(cleaned)
        if self._enhancement_provider == "gemini":
            if self._smoother is None:
                self._smoother = GeminiSmoother()
            cleaned = self._smoother.smooth(cleaned)
        return cleaned

    def transcribe(self, audio_f32: np.ndarray) -> str:
        raise NotImplementedError

    def open_stream(self) -> StreamSession:
        raise NotImplementedError


class _LocalTranscriber(_BaseTranscriber):
    def __init__(self, model_id: str, *, config: _EnhancementConfig, strip_fillers: bool = True) -> None:
        super().__init__(config=config, strip_fillers=strip_fillers)
        self.model_id = model_id
        self._model: parakeet_mlx.BaseParakeet | None = None

    def load(self) -> None:
        """Load the ASR model (downloads on first run, ~2 GB)."""
        if self._model is not None:
            return
        logger.info("Loading ASR model %s …", self.model_id)
        _prev_env = os.environ.get("HF_HUB_OFFLINE")
        _hf_constants = None
        _prev_const: bool | None = None
        try:
            import huggingface_hub.constants as _hf_constants  # type: ignore[import-not-found]

            _prev_const = getattr(_hf_constants, "HF_HUB_OFFLINE", None)
        except Exception:
            _hf_constants = None

        def _restore_offline_state() -> None:
            if _prev_env is None:
                os.environ.pop("HF_HUB_OFFLINE", None)
            else:
                os.environ["HF_HUB_OFFLINE"] = _prev_env
            if _hf_constants is not None and isinstance(_prev_const, bool):
                _hf_constants.HF_HUB_OFFLINE = _prev_const

        try:
            os.environ["HF_HUB_OFFLINE"] = "1"
            if _hf_constants is not None and isinstance(_prev_const, bool):
                _hf_constants.HF_HUB_OFFLINE = True
            self._model = parakeet_mlx.from_pretrained(self.model_id)
        except Exception as exc:
            logger.warning(
                "Offline model load failed for %s; falling back to online download: %s",
                self.model_id,
                exc,
            )
            logger.debug("Offline load traceback", exc_info=True)
            _restore_offline_state()
            self._model = parakeet_mlx.from_pretrained(self.model_id)
        finally:
            _restore_offline_state()
        logger.info("ASR model loaded")

    def unload(self) -> None:
        if self._model is None:
            return
        logger.info("Unloading ASR model %s …", self.model_id)
        self._model = None
        gc.collect()
        mx.clear_cache()
        logger.info("ASR model unloaded")

    @property
    def model(self) -> parakeet_mlx.BaseParakeet:
        if self._model is None:
            self.load()
        assert self._model is not None
        return self._model

    def transcribe(self, audio_f32: np.ndarray) -> str:
        audio_mx = mx.array(audio_f32)
        with self._open_stream() as stream:
            stream.add_audio(audio_mx)
            return self._clean_text(stream.result.text)

    def open_stream(self) -> StreamSession:
        return StreamSession(self._open_stream(), strip_fillers=self.strip_fillers)

    def _open_stream(self) -> parakeet_mlx.StreamingParakeet:
        return parakeet_mlx.StreamingParakeet(
            model=self.model,
            context_size=CONTEXT_SIZE,
            depth=STREAM_DEPTH,
        )


class _GroqTranscriber(_BaseTranscriber):
    def __init__(
        self,
        model_id: str,
        *,
        config: _EnhancementConfig,
        strip_fillers: bool = True,
    ) -> None:
        super().__init__(config=config, strip_fillers=strip_fillers)
        self.model_id = model_id
        self._client: Any | None = None

    def load(self) -> None:
        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set")

        try:
            from groq import Groq
        except Exception as exc:
            raise RuntimeError("Groq ASR requires the groq Python package") from exc

        self._client = Groq(api_key=api_key)
        logger.info("Groq ASR ready (%s).", self.model_id)

    def unload(self) -> None:
        self._client = None

    def transcribe(self, audio_f32: np.ndarray) -> str:
        if self._client is None:
            self.load()

        assert self._client is not None
        audio_file = _wav_upload_tuple(audio_f32)

        request: dict[str, Any] = {
            "file": audio_file,
            "model": self.model_id,
            "temperature": 0,
            "response_format": "verbose_json",
        }

        try:
            response = self._client.audio.transcriptions.create(**request)
        except Exception as exc:
            raise RuntimeError(f"Groq transcription failed: {exc}") from exc

        text = getattr(response, "text", None)
        if isinstance(text, str) and text.strip():
            return self._clean_text(text)

        response_dict = _response_to_dict(response)
        dict_text = response_dict.get("text")
        if not isinstance(dict_text, str):
            raise RuntimeError("Groq transcription response did not include text")
        return self._clean_text(dict_text)

    def open_stream(self) -> StreamSession:
        raise NotImplementedError("Streaming sessions are only available with the local ASR backend")


class Transcriber:
    """Speech-to-text façade supporting local Parakeet and Groq Whisper."""

    def __init__(
        self,
        asr_provider: ASRProvider = "parakeet",
        asr_model: str = DEFAULT_MODEL,
        *,
        strip_fillers: bool = True,
        enhancement_provider: EnhancementProvider = "none",
    ) -> None:
        validate_transcriber_settings(
            asr_provider=asr_provider,
            enhancement_provider=enhancement_provider,
        )
        config = _EnhancementConfig(provider=enhancement_provider)
        self.asr_provider = asr_provider
        self.model_id = resolve_model_id(asr_provider, asr_model)
        self._backend: _BaseTranscriber
        if asr_provider == "groq":
            self._backend = _GroqTranscriber(
                self.model_id,
                config=config,
                strip_fillers=strip_fillers,
            )
        else:
            self._backend = _LocalTranscriber(
                self.model_id,
                config=config,
                strip_fillers=strip_fillers,
            )

    def load(self) -> None:
        self._backend.load()

    def unload(self) -> None:
        self._backend.unload()

    def set_enhancement(self, provider: EnhancementProvider) -> None:
        validate_transcriber_settings(
            asr_provider=self.asr_provider,
            enhancement_provider=provider,
        )
        self._backend.set_enhancement(provider)

    def set_gemini_enabled(self, enabled: bool) -> None:
        self._backend.set_gemini_enabled(enabled)

    def transcribe(self, audio_f32: np.ndarray) -> str:
        return self._backend.transcribe(audio_f32)

    def open_stream(self) -> StreamSession:
        return self._backend.open_stream()


class StreamSession:
    """Wrapper around ``StreamingParakeet`` for ergonomic streaming use.

    Acts as a context manager that manages encoder attention mode.
    """

    def __init__(self, streamer: parakeet_mlx.StreamingParakeet, *, strip_fillers: bool = True) -> None:
        self._streamer = streamer
        self._regex_smoother = RegexSmoother() if strip_fillers else None

    def __enter__(self) -> StreamSession:
        self._streamer.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self._streamer.__exit__(*exc)

    def add_audio(self, chunk_f32: np.ndarray) -> None:
        self._streamer.add_audio(mx.array(chunk_f32))

    def _clean(self, text: str) -> str:
        if self._regex_smoother is None:
            return text
        return self._regex_smoother.smooth(text)

    @property
    def finalized_text(self) -> str:
        return self._clean("".join(t.text for t in self._streamer.finalized_tokens))

    @property
    def draft_text(self) -> str:
        return self._clean("".join(t.text for t in self._streamer.draft_tokens))

    @property
    def full_text(self) -> str:
        return self._clean(self._streamer.result.text.strip())

    @property
    def final_text(self) -> str:
        return self.full_text


def validate_transcriber_settings(
    *,
    asr_provider: ASRProvider,
    enhancement_provider: EnhancementProvider,
) -> None:
    if asr_provider not in {"parakeet", "groq"}:
        raise ValueError(f"Unsupported ASR provider: {asr_provider}")
    if enhancement_provider not in {"none", "gemini"}:
        raise ValueError(f"Unsupported enhancement provider: {enhancement_provider}")


def resolve_model_id(asr_provider: ASRProvider, model_id: str) -> str:
    normalized_model_id = model_id.strip()
    if normalized_model_id and normalized_model_id != DEFAULT_MODEL:
        return normalized_model_id
    if asr_provider == "groq":
        return GROQ_DEFAULT_MODEL
    return DEFAULT_MODEL


def _wav_upload_tuple(audio_f32: np.ndarray) -> tuple[str, bytes]:
    clipped = np.clip(audio_f32, -1.0, 1.0)
    pcm_i16 = (clipped * 32767.0).astype(np.int16)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_RATE)
        wf.writeframes(pcm_i16.tobytes())

    return ("dictation.wav", buffer.getvalue())


def _response_to_dict(response: object) -> dict[str, object]:
    if isinstance(response, dict):
        return response

    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        data = model_dump()
        if isinstance(data, dict):
            return data

    try:
        data = json.loads(str(response))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
