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
from typing import Callable

import numpy as np

from minidic.audio import AudioStream, TARGET_RATE, int16_to_float32
from minidic.inject import inject_text
from minidic.runtime.process import DAEMON_PID_FILE
from minidic.runtime.state import (
    clear_runtime_error,
    clear_runtime_state,
    write_runtime_error,
    write_runtime_state,
)
from minidic.settings import (
    get_groq_whisper_prompt,
    get_hotkey,
    get_hotkey_mode,
    get_polish,
    get_provider,
    get_recording_duration,
)
from minidic.transcribe import Transcriber

logger = logging.getLogger(__name__)

_MINIDIC_DIR = Path.home() / ".minidic"
_MODEL_IDLE_UNLOAD_SECONDS = 30 * 60
# Recordings shorter than this are silently discarded (accidental taps).
_MIN_RECORDING_SECONDS = 0.5


def _hotkey_listener_kwargs(hotkey_mode: str) -> dict[str, float | bool]:
    return {
        "press_debounce_seconds": 0.05 if hotkey_mode == "push_to_talk" else 0.3,
        "modifier_press_on_release": hotkey_mode == "toggle",
    }


class _HotkeyListenerBinding:
    def __init__(
        self,
        *,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        listener_factory: Callable[..., object],
    ) -> None:
        self._on_press = on_press
        self._on_release = on_release
        self._listener_factory = listener_factory
        self._listener = None
        self._hotkey: str | None = None
        self._hotkey_mode: str | None = None
        self._lock = threading.Lock()

    def start(self, *, hotkey: str, hotkey_mode: str) -> None:
        listener = self._listener_factory(
            on_press=self._on_press,
            on_release=self._on_release,
            hotkey=hotkey,
            **_hotkey_listener_kwargs(hotkey_mode),
        )
        listener.start()
        with self._lock:
            self._listener = listener
            self._hotkey = hotkey
            self._hotkey_mode = hotkey_mode

    def reload_if_needed(self) -> bool:
        desired_hotkey = get_hotkey()
        desired_hotkey_mode = get_hotkey_mode()
        with self._lock:
            if (
                desired_hotkey == self._hotkey
                and desired_hotkey_mode == self._hotkey_mode
            ):
                return False
            current_listener = self._listener

        listener = self._listener_factory(
            on_press=self._on_press,
            on_release=self._on_release,
            hotkey=desired_hotkey,
            **_hotkey_listener_kwargs(desired_hotkey_mode),
        )
        listener.start()

        with self._lock:
            self._listener = listener
            self._hotkey = desired_hotkey
            self._hotkey_mode = desired_hotkey_mode

        if current_listener is not None:
            current_listener.stop()

        return True

    def stop(self) -> None:
        with self._lock:
            listener = self._listener
            self._listener = None
        if listener is not None:
            listener.stop()

    def get_hotkey_mode(self) -> str | None:
        with self._lock:
            return self._hotkey_mode


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

    whisper_prompt = get_groq_whisper_prompt()
    hotkey = get_hotkey()
    hotkey_mode = get_hotkey_mode()
    logger.debug("Loaded Groq Whisper prompt at daemon start: %r", whisper_prompt)
    transcriber = Transcriber(
        provider=get_provider(),
        polish=get_polish(),
        prompt=whisper_prompt,
    )
    model_loaded = False
    last_model_use: float | None = None
    model_lock = threading.Lock()
    backend_name = "Groq ASR" if transcriber.provider == "whisper" else "ASR model"
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
                current_mode = mode
                if current_mode == "recording":
                    recording_chunks.append(chunk)
                    sample_count += len(chunk)
                    if sample_count >= max_speech_samples:
                        finish_event.set()
                elif current_mode == "draining":
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
            chunks = list(recording_chunks)
            recording_chunks.clear()
            sample_count = 0
            captured_samples = sum(len(c) for c in chunks)
            if audio is not None:
                audio.stop()
                audio = None
            logger.info("Recording stopped (mic closed).")

            if captured_samples / TARGET_RATE < _MIN_RECORDING_SECONDS:
                mode = "idle"
                _write_state("idle")
                logger.info(
                    "Recording too short (%.2fs < %.2fs) — discarding.",
                    captured_samples / TARGET_RATE,
                    _MIN_RECORDING_SECONDS,
                )
                return

            mode = "transcribing"
            _write_state("transcribing")

        threading.Thread(
            target=_transcribe_and_inject,
            args=(chunks,),
            name="transcriber",
            daemon=True,
        ).start()

    def _transcriber_signature(current: Transcriber) -> tuple[str, str]:
        return (
            current.provider,
            current.model_id,
        )

    def _ensure_transcriber_current() -> None:
        nonlocal transcriber, model_loaded, last_model_use, backend_name

        desired = Transcriber(provider=get_provider(), polish=False, prompt=whisper_prompt)
        if _transcriber_signature(desired) == _transcriber_signature(transcriber):
            return

        if model_loaded:
            transcriber.unload()
            model_loaded = False
            last_model_use = None

        transcriber = desired
        backend_name = "Groq ASR" if transcriber.provider == "whisper" else "ASR model"
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

                transcriber.set_polish(get_polish())
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
                    try:
                        clear_runtime_error()
                    except OSError:
                        logger.exception("Failed to clear error file")

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

    listener_binding: _HotkeyListenerBinding

    def on_press() -> None:
        nonlocal max_speech_samples, sample_count, mode, audio

        current_hotkey_mode = listener_binding.get_hotkey_mode()

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
            elif mode == "recording" and current_hotkey_mode == "toggle":
                finish_event.set()
            else:
                logger.debug("Hotkey press ignored — transcription in progress")

    def on_release() -> None:
        current_hotkey_mode = listener_binding.get_hotkey_mode()
        if current_hotkey_mode != "push_to_talk":
            return

        with lock:
            current_mode = mode

        if current_mode == "recording":
            finish_event.set()

    def _hotkey_listener_reloader() -> None:
        while not shutdown.wait(0.5):
            with lock:
                if mode != "idle":
                    continue

            try:
                if not listener_binding.reload_if_needed():
                    continue
                logger.info(
                    "Reloaded hotkey listener — %s to dictate (%s mode).",
                    get_hotkey(),
                    listener_binding.get_hotkey_mode(),
                )
            except Exception:
                logger.exception("Failed to reload hotkey listener")

    threading.Thread(target=_audio_pump, name="audio-pump", daemon=True).start()
    threading.Thread(target=_model_reaper, name="model-reaper", daemon=True).start()

    listener_binding = _HotkeyListenerBinding(
        on_press=on_press,
        on_release=on_release,
        listener_factory=GlobalHotkeyListener,
    )
    listener_binding.start(hotkey=hotkey, hotkey_mode=hotkey_mode)
    threading.Thread(
        target=_hotkey_listener_reloader,
        name="hotkey-listener-reloader",
        daemon=True,
    ).start()

    DAEMON_PID_FILE.write_text(str(os.getpid()))
    _write_state("idle")
    logger.info("Daemon ready — %s to dictate (%s mode).", hotkey, hotkey_mode)

    shutdown.wait()

    logger.info("Shutting down …")
    with lock:
        if audio is not None:
            audio.stop()
            audio = None
    with model_lock:
        if model_loaded:
            transcriber.unload()
    listener_binding.stop()
    clear_runtime_state()
