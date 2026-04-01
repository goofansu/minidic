"""Energy-based Voice Activity Detection for auto-stop dictation."""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16_000
_CALIBRATION_DURATION = 0.5  # seconds of ambient audio used to set the noise floor
_SPEECH_RATIO = 4.0  # speech_threshold = noise_floor × _SPEECH_RATIO
_MIN_NOISE_FLOOR = 50.0  # int16 RMS floor — prevents false positives in digital silence
_MIN_SPEECH_DURATION = 0.5  # seconds of speech required before auto-stop can fire

DEFAULT_SILENCE_DURATION = 1.5  # seconds of silence to trigger auto-stop


class VADFilter:
    """Stateful energy-based Voice Activity Detector.

    Processes streaming 16 kHz int16 audio chunks of any size.

    Phase 1 — Calibration (first ``_CALIBRATION_DURATION`` seconds):
        Measures ambient RMS across chunks and computes a speech threshold as
        ``max(noise_floor_10th_percentile, _MIN_NOISE_FLOOR) × _SPEECH_RATIO``.
        Using the 10th percentile makes calibration robust when the speaker
        starts talking before calibration is complete.

    Phase 2 — Detection:
        Each chunk is classified as speech (RMS ≥ threshold) or silence.
        Auto-stop fires when **both** conditions hold:
        * At least ``_MIN_SPEECH_DURATION`` of cumulative speech has been heard.
        * At least ``silence_duration`` of *continuous* silence has followed.
        Speech resuming after a pause resets the silence counter.

    Parameters
    ----------
    silence_duration:
        Seconds of sustained silence required to trigger auto-stop.

    Usage::

        vad = VADFilter(silence_duration=1.5)
        vad.reset()  # call before each new recording
        for chunk in audio_stream:
            if vad.process(chunk):
                break  # auto-stop triggered
    """

    def __init__(self, silence_duration: float = DEFAULT_SILENCE_DURATION) -> None:
        self._silence_threshold_samples = int(silence_duration * _SAMPLE_RATE)
        self._calibration_samples = int(_CALIBRATION_DURATION * _SAMPLE_RATE)
        self._min_speech_samples = int(_MIN_SPEECH_DURATION * _SAMPLE_RATE)

        self._calibration_rms: list[float] = []
        self._calibration_done = False
        self._speech_threshold: float = 0.0

        self._speech_samples: int = 0
        self._silence_samples: int = 0
        self._has_speech: bool = False

    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reset all internal state; call before starting a new recording."""
        self._calibration_rms.clear()
        self._calibration_done = False
        self._speech_threshold = 0.0
        self._speech_samples = 0
        self._silence_samples = 0
        self._has_speech = False

    # ------------------------------------------------------------------

    @staticmethod
    def _chunk_rms(chunk: np.ndarray) -> float:
        f32 = chunk.astype(np.float32)
        return float(np.sqrt(np.mean(f32 * f32)))

    def process(self, chunk: np.ndarray) -> bool:
        """Process one 16 kHz int16 chunk.

        Returns ``True`` when auto-stop should be triggered, ``False`` otherwise.
        Safe to call after ``True`` is returned — will keep returning ``True``.
        """
        rms = self._chunk_rms(chunk)
        n = len(chunk)

        # ---- Phase 1: calibration ----------------------------------------
        if not self._calibration_done:
            self._calibration_rms.append(rms)
            if len(self._calibration_rms) * n >= self._calibration_samples:
                noise_floor = max(
                    float(np.quantile(self._calibration_rms, 0.10)),
                    _MIN_NOISE_FLOOR,
                )
                self._speech_threshold = noise_floor * _SPEECH_RATIO
                self._calibration_done = True
                logger.debug(
                    "VAD calibrated: noise_floor=%.0f  threshold=%.0f",
                    noise_floor,
                    self._speech_threshold,
                )
            return False

        # ---- Phase 2: detection ------------------------------------------
        if rms >= self._speech_threshold:
            self._speech_samples += n
            self._silence_samples = 0
            self._has_speech = True
        elif self._has_speech:
            self._silence_samples += n
            if (
                self._speech_samples >= self._min_speech_samples
                and self._silence_samples >= self._silence_threshold_samples
            ):
                logger.debug(
                    "VAD auto-stop: %.2fs speech then %.2fs silence",
                    self._speech_samples / _SAMPLE_RATE,
                    self._silence_samples / _SAMPLE_RATE,
                )
                return True

        return False

    # ------------------------------------------------------------------

    @property
    def silence_elapsed(self) -> float:
        """Seconds of continuous silence since last speech (0 while speaking or before speech)."""
        return self._silence_samples / _SAMPLE_RATE

    @property
    def speech_elapsed(self) -> float:
        """Total seconds of detected speech."""
        return self._speech_samples / _SAMPLE_RATE

    @property
    def is_calibrated(self) -> bool:
        """True once the ambient noise floor has been measured."""
        return self._calibration_done
