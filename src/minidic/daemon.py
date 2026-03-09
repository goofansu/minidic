"""Dictation daemon lifecycle and internals."""

from __future__ import annotations

import argparse
import logging
import os
import queue as _queue
import signal
import threading
import time
import wave
from pathlib import Path

import numpy as np

from minidic.audio import AudioStream, TARGET_RATE, int16_to_float32
from minidic.inject import inject_text
from minidic.runtime.process import DAEMON_PID_FILE
from minidic.runtime.state import clear_runtime_state, write_runtime_error, write_runtime_state
from minidic.settings import get_asr_provider, get_polish_provider, get_recording_duration
from minidic.transcribe import Transcriber

logger = logging.getLogger(__name__)

_MINIDIC_DIR = Path.home() / ".minidic"
_MODEL_IDLE_UNLOAD_SECONDS = 30 * 60


def _save_wav(chunks: list[np.ndarray]) -> Path:
    recordings_dir = _MINIDIC_DIR / "recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)

    ts_ms = int(time.time() * 1000)
    wav_path = recordings_dir / f"{ts_ms}.wav"
    while wav_path.exists():
        ts_ms += 1
        wav_path = recordings_dir / f"{ts_ms}.wav"

    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_RATE)
        for c in chunks:
            wf.writeframes(c.tobytes())

    return wav_path


def run_daemon(args: argparse.Namespace) -> None:
    """Global-hotkey dictation daemon (runs in foreground)."""
    from minidic.hotkey import GlobalHotkeyListener

    shutdown = threading.Event()

    def _on_sigterm(signum: int, frame: object) -> None:
        shutdown.set()

    signal.signal(signal.SIGTERM, _on_sigterm)

    transcriber = Transcriber(
        asr_provider=get_asr_provider(),
        polish_provider=get_polish_provider(),
    )
    model_loaded = False
    last_model_use: float | None = None
    model_lock = threading.Lock()
    backend_name = "Groq ASR" if transcriber.asr_provider == "groq" else "ASR model"
    logger.info("%s will load on first transcription (%s).", backend_name, transcriber.model_id)

    max_speech_samples = int(get_recording_duration(default=args.duration) * TARGET_RATE)

    audio: AudioStream | None = None
    recording_chunks: list[np.ndarray] = []
    sample_count = 0
    mode = "idle"
    lock = threading.Lock()

    def _write_state(state: str) -> None:
        try:
            write_runtime_state(state)
        except OSError:
            logger.exception("Failed to write state file")

    def _write_error_state(message: str) -> None:
        try:
            write_runtime_error(message)
            write_runtime_state("error")
        except OSError:
            logger.exception("Failed to write error state")

    finish_event = threading.Event()

    def _audio_pump() -> None:
        nonlocal sample_count, mode
        while not shutdown.is_set():
            with lock:
                current_audio = audio
            if current_audio is None:
                time.sleep(0.05)
                continue
            try:
                chunk = current_audio.read(timeout=0.5)
            except _queue.Empty:
                continue

            with lock:
                if mode == "recording":
                    recording_chunks.append(chunk)
                    sample_count += len(chunk)
                    if sample_count >= max_speech_samples:
                        finish_event.set()
                elif mode == "draining":
                    recording_chunks.append(chunk)

            if finish_event.is_set():
                finish_event.clear()
                threading.Thread(target=_finish_recording, name="finish-rec", daemon=True).start()

    def _finish_recording() -> None:
        nonlocal sample_count, mode, audio

        with lock:
            if mode != "recording":
                return
            mode = "draining"

        time.sleep(0.05)

        with lock:
            mode = "transcribing"
            _write_state("transcribing")
            chunks = list(recording_chunks)
            recording_chunks.clear()
            sample_count = 0
            if audio is not None:
                audio.stop()
                audio = None
            logger.info("Recording stopped (mic closed).")

        threading.Thread(
            target=_transcribe_and_inject,
            args=(chunks,),
            name="transcriber",
            daemon=True,
        ).start()

    def _transcriber_signature(current: Transcriber) -> tuple[str, str]:
        return (
            current.asr_provider,
            current.model_id,
        )

    def _ensure_transcriber_current() -> None:
        nonlocal transcriber, model_loaded, last_model_use, backend_name

        desired = Transcriber(asr_provider=get_asr_provider(), polish_provider="none")
        if _transcriber_signature(desired) == _transcriber_signature(transcriber):
            return

        if model_loaded:
            transcriber.unload()
            model_loaded = False
            last_model_use = None

        transcriber = desired
        backend_name = "Groq ASR" if transcriber.asr_provider == "groq" else "ASR model"
        logger.info("Switched to %s (%s).", backend_name, transcriber.model_id)

    def _transcribe_and_inject(chunks: list[np.ndarray]) -> None:
        nonlocal mode, model_loaded, last_model_use
        caught_exc: BaseException | None = None
        try:
            if not chunks:
                return

            wav_path = _save_wav(chunks)
            logger.info("Saved %s", wav_path)

            audio_f32 = int16_to_float32(np.concatenate(chunks))
            duration = len(audio_f32) / TARGET_RATE
            logger.info("Transcribing %.1fs …", duration)

            with model_lock:
                _ensure_transcriber_current()
                if not model_loaded:
                    logger.info("Loading %s (%s) …", backend_name, transcriber.model_id)
                    transcriber.load()
                    model_loaded = True
                    logger.info("%s ready.", backend_name)

                transcriber.set_polish(get_polish_provider())

                text = transcriber.transcribe(audio_f32)
                last_model_use = time.monotonic()

            if text.strip():
                inject_text(text)
                logger.info("Injected: %s", text)
            else:
                logger.info("No speech detected.")
        except Exception as exc:
            logger.exception("Transcription/injection error")
            caught_exc = exc
        finally:
            with lock:
                mode = "idle"
                if caught_exc is not None:
                    _write_error_state(str(caught_exc))
                else:
                    _write_state("idle")

    def _model_reaper() -> None:
        nonlocal model_loaded, last_model_use
        while not shutdown.wait(5.0):
            with lock:
                if mode != "idle":
                    continue

            with model_lock:
                if not model_loaded or last_model_use is None:
                    continue
                idle_for = time.monotonic() - last_model_use
                if idle_for < _MODEL_IDLE_UNLOAD_SECONDS:
                    continue
                transcriber.unload()
                model_loaded = False
                last_model_use = None
                logger.info("%s unloaded after %.0fs idle.", backend_name, idle_for)

    def on_hotkey() -> None:
        nonlocal max_speech_samples, sample_count, mode, audio

        with lock:
            if mode == "idle":
                recording_chunks.clear()
                sample_count = 0
                max_speech_samples = int(get_recording_duration(default=args.duration) * TARGET_RATE)
                try:
                    stream = AudioStream()
                    stream.start()
                except Exception as exc:
                    logger.exception("Failed to open microphone")
                    _write_error_state(str(exc))
                    return
                audio = stream
                mode = "recording"
                _write_state("recording")
                logger.info("Recording started (mic opened).")
            elif mode == "recording":
                finish_event.set()
            else:
                logger.debug("Hotkey ignored — transcription in progress")

    threading.Thread(target=_audio_pump, name="audio-pump", daemon=True).start()
    threading.Thread(target=_model_reaper, name="model-reaper", daemon=True).start()

    listener = GlobalHotkeyListener(on_hotkey=on_hotkey)
    listener.start()

    DAEMON_PID_FILE.write_text(str(os.getpid()))
    _write_state("idle")
    logger.info("Daemon ready — F5 to dictate.")

    shutdown.wait()

    logger.info("Shutting down …")
    with lock:
        if audio is not None:
            audio.stop()
            audio = None
    with model_lock:
        if model_loaded:
            transcriber.unload()
    listener.stop()
    clear_runtime_state()
