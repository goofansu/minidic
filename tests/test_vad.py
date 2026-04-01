"""Tests for VADFilter (energy-based voice activity detection)."""

from __future__ import annotations

import numpy as np
import pytest

from minidic.vad import VADFilter, _SAMPLE_RATE, DEFAULT_SILENCE_DURATION

CHUNK = 512  # matches audio.BLOCKSIZE — 32 ms at 16 kHz


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _silence(n_samples: int = CHUNK) -> np.ndarray:
    """Near-silent int16 audio (RMS ≈ 0)."""
    return np.zeros(n_samples, dtype=np.int16)


def _speech(n_samples: int = CHUNK, amplitude: int = 8_000) -> np.ndarray:
    """Synthetic speech-like int16 audio (sine wave, high amplitude)."""
    t = np.arange(n_samples)
    return (np.sin(2 * np.pi * 300 * t / _SAMPLE_RATE) * amplitude).astype(np.int16)


def _calibrate(vad: VADFilter, chunk_size: int = CHUNK) -> None:
    """Feed silence until calibration completes."""
    calibration_chunks = int(0.5 * _SAMPLE_RATE / chunk_size) + 2
    for _ in range(calibration_chunks):
        vad.process(_silence(chunk_size))


# ---------------------------------------------------------------------------
# Calibration phase
# ---------------------------------------------------------------------------


class TestCalibration:
    def test_returns_false_during_calibration(self):
        vad = VADFilter()
        # Feed slightly fewer chunks than needed for calibration
        n = max(1, int(0.5 * _SAMPLE_RATE / CHUNK) - 1)
        for _ in range(n):
            assert vad.process(_silence()) is False

    def test_calibration_completes_after_enough_audio(self):
        vad = VADFilter()
        assert not vad.is_calibrated
        _calibrate(vad)
        assert vad.is_calibrated

    def test_calibration_robust_to_speech_during_window(self):
        """10th-percentile noise floor should stay low even with speech bursts."""
        vad = VADFilter()
        n = int(0.5 * _SAMPLE_RATE / CHUNK) + 2
        for i in range(n):
            # First half is speech, second half is silence
            chunk = _speech() if i < n // 2 else _silence()
            vad.process(chunk)
        assert vad.is_calibrated

    def test_no_auto_stop_fires_during_calibration(self):
        vad = VADFilter(silence_duration=0.3)
        n = int(0.5 * _SAMPLE_RATE / CHUNK) + 2
        for _ in range(n):
            assert vad.process(_speech()) is False


# ---------------------------------------------------------------------------
# Detection phase
# ---------------------------------------------------------------------------


class TestDetection:
    def test_speech_then_long_silence_triggers_stop(self):
        vad = VADFilter(silence_duration=1.0)
        _calibrate(vad)

        # 1 s of speech
        for _ in range(int(1.0 * _SAMPLE_RATE / CHUNK)):
            vad.process(_speech())

        # Feed silence until stop (allow up to 2 s)
        fired = False
        for _ in range(int(2.0 * _SAMPLE_RATE / CHUNK)):
            if vad.process(_silence()):
                fired = True
                break

        assert fired

    def test_silence_alone_never_triggers_stop(self):
        vad = VADFilter(silence_duration=1.0)
        _calibrate(vad)

        for _ in range(int(5.0 * _SAMPLE_RATE / CHUNK)):
            assert vad.process(_silence()) is False

    def test_min_speech_guard_prevents_early_stop(self):
        """< 0.5 s of speech should not allow auto-stop, even with long silence."""
        vad = VADFilter(silence_duration=1.0)
        _calibrate(vad)

        # 0.2 s of speech — below the 0.5 s minimum
        for _ in range(int(0.2 * _SAMPLE_RATE / CHUNK)):
            vad.process(_speech())

        # 3 s of silence
        for _ in range(int(3.0 * _SAMPLE_RATE / CHUNK)):
            assert vad.process(_silence()) is False

    def test_speech_resuming_resets_silence_counter(self):
        vad = VADFilter(silence_duration=1.0)
        _calibrate(vad)

        # Enough speech to pass minimum
        for _ in range(int(0.6 * _SAMPLE_RATE / CHUNK)):
            vad.process(_speech())

        # 0.8 s silence (just below threshold)
        for _ in range(int(0.8 * _SAMPLE_RATE / CHUNK)):
            vad.process(_silence())

        # Speech resumes — resets silence counter
        for _ in range(int(0.3 * _SAMPLE_RATE / CHUNK)):
            vad.process(_speech())

        assert vad.silence_elapsed == 0.0

        # Now the counter restarts; 1.5 s silence should fire
        fired = False
        for _ in range(int(2.0 * _SAMPLE_RATE / CHUNK)):
            if vad.process(_silence()):
                fired = True
                break
        assert fired

    def test_stop_fires_faster_with_shorter_silence_duration(self):
        vad_short = VADFilter(silence_duration=0.5)
        vad_long = VADFilter(silence_duration=2.0)

        for vad in (vad_short, vad_long):
            _calibrate(vad)
            for _ in range(int(1.0 * _SAMPLE_RATE / CHUNK)):
                vad.process(_speech())

        chunks_to_stop_short = 0
        for _ in range(int(3.0 * _SAMPLE_RATE / CHUNK)):
            chunks_to_stop_short += 1
            if vad_short.process(_silence()):
                break

        chunks_to_stop_long = 0
        for _ in range(int(5.0 * _SAMPLE_RATE / CHUNK)):
            chunks_to_stop_long += 1
            if vad_long.process(_silence()):
                break

        assert chunks_to_stop_short < chunks_to_stop_long


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_speech_elapsed_tracks_cumulative_speech(self):
        vad = VADFilter()
        _calibrate(vad)

        for _ in range(int(1.0 * _SAMPLE_RATE / CHUNK)):
            vad.process(_speech())

        assert vad.speech_elapsed >= 0.9

    def test_silence_elapsed_tracks_continuous_silence_after_speech(self):
        vad = VADFilter()
        _calibrate(vad)

        for _ in range(int(0.6 * _SAMPLE_RATE / CHUNK)):
            vad.process(_speech())

        for _ in range(int(0.4 * _SAMPLE_RATE / CHUNK)):
            vad.process(_silence())

        assert vad.silence_elapsed >= 0.3

    def test_silence_elapsed_is_zero_before_speech(self):
        vad = VADFilter()
        _calibrate(vad)
        assert vad.silence_elapsed == 0.0

    def test_silence_elapsed_is_zero_while_speaking(self):
        vad = VADFilter()
        _calibrate(vad)

        for _ in range(int(0.5 * _SAMPLE_RATE / CHUNK)):
            vad.process(_speech())

        assert vad.silence_elapsed == 0.0

    def test_speech_elapsed_is_zero_before_calibration(self):
        vad = VADFilter()
        assert vad.speech_elapsed == 0.0

    def test_default_silence_duration_constant(self):
        assert DEFAULT_SILENCE_DURATION == 1.5


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_clears_calibration(self):
        vad = VADFilter()
        _calibrate(vad)
        assert vad.is_calibrated
        vad.reset()
        assert not vad.is_calibrated

    def test_reset_clears_speech_and_silence_counters(self):
        vad = VADFilter()
        _calibrate(vad)
        for _ in range(int(0.6 * _SAMPLE_RATE / CHUNK)):
            vad.process(_speech())
        for _ in range(int(0.4 * _SAMPLE_RATE / CHUNK)):
            vad.process(_silence())

        vad.reset()

        assert vad.speech_elapsed == 0.0
        assert vad.silence_elapsed == 0.0

    def test_reset_allows_reuse_for_next_recording(self):
        vad = VADFilter(silence_duration=1.0)
        _calibrate(vad)
        for _ in range(int(1.0 * _SAMPLE_RATE / CHUNK)):
            vad.process(_speech())

        vad.reset()
        _calibrate(vad)

        # Fresh recording: silence alone should not trigger
        for _ in range(int(3.0 * _SAMPLE_RATE / CHUNK)):
            assert vad.process(_silence()) is False
