"""Speech-to-text transcription using parakeet-mlx."""

from __future__ import annotations

import gc
import logging
import os
import re

import mlx.core as mx
import numpy as np
import parakeet_mlx

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"
CONTEXT_SIZE = (256, 256)
STREAM_DEPTH = 1

# Filler words / hesitation sounds to strip from transcription output.
# Matched case-insensitively as whole words.
FILLER_WORDS = frozenset({
    "um", "uh", "uhh", "umm", "erm", "er", "ah", "ahh",
    "hm", "hmm", "huh", "mm", "mmm", "mhm",
})

_FILLER_PATTERN = re.compile(
    r"\b("
    + "|".join(re.escape(w) for w in sorted(FILLER_WORDS, key=len, reverse=True))
    + r")\b[,;]?\s*",
    re.IGNORECASE,
)


def remove_fillers(text: str) -> str:
    """Remove filler words (um, uh, etc.) and clean up residual whitespace/punctuation."""
    # Remove filler words along with any trailing comma/semicolon
    cleaned = _FILLER_PATTERN.sub(" ", text)
    # Collapse runs of whitespace
    cleaned = re.sub(r"  +", " ", cleaned)
    # Remove leading comma/semicolon (if filler was at sentence start)
    cleaned = re.sub(r"^\s*[,;]\s*", "", cleaned)
    return cleaned.strip()


class Transcriber:
    """Loads a parakeet-mlx model and provides streaming transcription.

    Parameters
    ----------
    model_id:
        Hugging Face model id or local path (default: parakeet-tdt-0.6b-v3).
    """

    def __init__(self, model_id: str = DEFAULT_MODEL, *, strip_fillers: bool = True) -> None:
        self.model_id = model_id
        self.strip_fillers = strip_fillers
        self._model: parakeet_mlx.BaseParakeet | None = None

    def load(self) -> None:
        """Load the ASR model (downloads on first run, ~2 GB)."""
        if self._model is not None:
            return
        logger.info("Loading ASR model %s …", self.model_id)
        # parakeet_mlx.from_pretrained does not expose a local_files_only
        # parameter, so we force huggingface_hub into offline mode first,
        # then fall back to online if cache is incomplete.
        #
        # Note: huggingface_hub computes offline mode at import-time via
        # constants.HF_HUB_OFFLINE, so mutating os.environ alone can be too
        # late. We update both env and the constants flag temporarily.
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
            # Not cached yet — restore flags and allow network download.
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
        """Unload the ASR model and release cached MLX memory."""
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
        """Transcribe a complete audio segment (non-streaming).

        Parameters
        ----------
        audio_f32:
            1-D float32 numpy array at 16 kHz.

        Returns
        -------
        The transcribed text.
        """
        audio_mx = mx.array(audio_f32)
        with self._open_stream() as stream:
            stream.add_audio(audio_mx)
            text = stream.result.text.strip()
            return remove_fillers(text) if self.strip_fillers else text

    def open_stream(self) -> StreamSession:
        """Open a streaming transcription session.

        Usage::

            session = transcriber.open_stream()
            with session:
                session.add_audio(chunk1)
                print(session.draft_text)
                session.add_audio(chunk2)
                ...
            final = session.final_text
        """
        return StreamSession(self._open_stream(), strip_fillers=self.strip_fillers)

    def _open_stream(self) -> parakeet_mlx.StreamingParakeet:
        return parakeet_mlx.StreamingParakeet(
            model=self.model,
            context_size=CONTEXT_SIZE,
            depth=STREAM_DEPTH,
        )


class StreamSession:
    """Wrapper around ``StreamingParakeet`` for ergonomic streaming use.

    Acts as a context manager that manages encoder attention mode.
    """

    def __init__(self, streamer: parakeet_mlx.StreamingParakeet, *, strip_fillers: bool = True) -> None:
        self._streamer = streamer
        self._strip_fillers = strip_fillers

    def __enter__(self) -> StreamSession:
        self._streamer.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self._streamer.__exit__(*exc)

    def add_audio(self, chunk_f32: np.ndarray) -> None:
        """Feed a float32 audio chunk (1-D numpy array at 16 kHz)."""
        self._streamer.add_audio(mx.array(chunk_f32))

    def _clean(self, text: str) -> str:
        return remove_fillers(text) if self._strip_fillers else text

    @property
    def finalized_text(self) -> str:
        """Text from tokens that are confirmed (won't change)."""
        return self._clean("".join(t.text for t in self._streamer.finalized_tokens))

    @property
    def draft_text(self) -> str:
        """Tentative text that may still change on next chunk."""
        return self._clean("".join(t.text for t in self._streamer.draft_tokens))

    @property
    def full_text(self) -> str:
        """Finalized + draft text combined."""
        return self._clean(self._streamer.result.text.strip())

    @property
    def final_text(self) -> str:
        """Alias for full_text — call after the stream is closed."""
        return self.full_text
