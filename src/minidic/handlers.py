"""CLI command handlers for minidic."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np

from minidic.audio import AudioStream, TARGET_RATE, int16_to_float32
from minidic.daemon import run_daemon
from minidic.runtime.process import (
    DAEMON_LOG_FILE,
    DAEMON_PID_FILE,
    MENUBAR_LOG_FILE,
    MENUBAR_PID_FILE,
    build_minidic_command,
    clear_runtime_state,
    ensure_runtime_dirs,
    read_menubar_pid,
    spawn_detached,
)
from minidic.transcribe import Transcriber

logger = logging.getLogger(__name__)

_MINIDIC_DIR = Path.home() / ".minidic"


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


def setup_logging(verbose: bool, *, to_file: bool = False) -> None:
    kwargs: dict = dict(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    if to_file:
        ensure_runtime_dirs()
        kwargs["filename"] = str(DAEMON_LOG_FILE)
        kwargs["filemode"] = "w"
    logging.basicConfig(**kwargs)


def run_interactive(args: argparse.Namespace) -> None:
    setup_logging(args.verbose)

    transcriber = Transcriber(model_id=args.model, smooth_with_gemini=args.gemini)
    print(f"Loading ASR model ({args.model}) …", flush=True)
    transcriber.load()
    print("ASR model ready.", flush=True)

    max_speech_samples = int(args.duration * TARGET_RATE)
    print("Ready. Enter to record, Ctrl+C to stop.", flush=True)

    try:
        while True:
            sys.stdin.readline()
            print("\033[A\033[K", end="", flush=True)

            chunks: list[np.ndarray] = []
            sample_count = 0

            try:
                with AudioStream() as audio:
                    while sample_count < max_speech_samples:
                        chunk = audio.read(timeout=2.0)
                        chunks.append(chunk)
                        sample_count += len(chunk)
                        elapsed = sample_count / TARGET_RATE
                        print(f"\r\033[K🎤 {elapsed:.1f}s", end="", flush=True)
            except KeyboardInterrupt:
                pass

            if not chunks:
                print("\r\033[K", end="", flush=True)
                continue

            dur = sample_count / TARGET_RATE
            wav_path = _save_wav(chunks)
            logger.info("Saved %s (%.1fs)", wav_path, dur)

            print(f"\r\033[K⏳ Transcribing {dur:.1f}s …", end="", flush=True)
            audio_f32 = int16_to_float32(np.concatenate(chunks))
            text = transcriber.transcribe(audio_f32)

            if text.strip():
                print(f"\r\033[K✅ {text}", flush=True)
            else:
                print("\r\033[K❌ (no speech detected)", flush=True)

    except (KeyboardInterrupt, EOFError):
        print("\nBye.", flush=True)


def cmd_daemon_foreground(args: argparse.Namespace) -> None:
    ensure_runtime_dirs()
    setup_logging(args.verbose, to_file=True)

    try:
        run_daemon(args)
    except Exception:
        logger.exception("Daemon crashed")
        sys.exit(1)
    finally:
        DAEMON_PID_FILE.unlink(missing_ok=True)
        clear_runtime_state()


def cmd_menubar(args: argparse.Namespace) -> None:
    existing = read_menubar_pid()
    if existing is not None:
        print(f"Menu bar app already running (pid {existing}).", flush=True)
        sys.exit(1)

    ensure_runtime_dirs()
    cmd = build_minidic_command(args, "_menubar")

    with MENUBAR_LOG_FILE.open("a", encoding="utf-8") as log_file:
        proc = spawn_detached(cmd, stdout=log_file, stderr=log_file)

    start_time = time.monotonic()
    while time.monotonic() - start_time < 2.0:
        rc = proc.poll()
        if rc is not None:
            print(
                f"Menu bar app failed to start (exit code {rc}). Check log: {MENUBAR_LOG_FILE}",
                flush=True,
            )
            sys.exit(1)

        running_pid = read_menubar_pid()
        if running_pid is not None:
            print(f"Menu bar app launched (pid {running_pid}).", flush=True)
            return

        time.sleep(0.1)

    rc = proc.poll()
    if rc is not None:
        print(
            f"Menu bar app failed to start (exit code {rc}). Check log: {MENUBAR_LOG_FILE}",
            flush=True,
        )
        sys.exit(1)

    proc.terminate()
    print(
        "Menu bar app launch timed out before readiness signal. "
        f"Check log: {MENUBAR_LOG_FILE}",
        flush=True,
    )
    sys.exit(1)


def cmd_menubar_foreground(args: argparse.Namespace) -> None:
    from minidic.menubar import run_menubar

    existing = read_menubar_pid()
    if existing is not None and existing != os.getpid():
        print(f"Menu bar app already running (pid {existing}).", flush=True)
        sys.exit(1)

    ensure_runtime_dirs()
    MENUBAR_PID_FILE.write_text(str(os.getpid()))
    try:
        run_menubar(args)
    finally:
        MENUBAR_PID_FILE.unlink(missing_ok=True)


def cmd_transcribe(args: argparse.Namespace) -> None:
    import soxr

    setup_logging(args.verbose)

    wav_path = Path(args.file)
    if not wav_path.exists():
        print(f"Error: file not found: {wav_path}", file=sys.stderr)
        sys.exit(1)

    with wave.open(str(wav_path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    logger.info(
        "%s: %d ch, %d-bit, %d Hz, %.1fs",
        wav_path.name,
        n_channels,
        sampwidth * 8,
        framerate,
        n_frames / framerate,
    )

    if sampwidth == 2:
        audio_i16 = np.frombuffer(raw, dtype=np.int16)
    elif sampwidth == 1:
        audio_u8 = np.frombuffer(raw, dtype=np.uint8)
        audio_i16 = ((audio_u8.astype(np.int16) - 128) * 256).astype(np.int16)
    elif sampwidth == 4:
        audio_i32 = np.frombuffer(raw, dtype=np.int32)
        audio_i16 = (audio_i32 >> 16).astype(np.int16)
    else:
        print(f"Error: unsupported sample width: {sampwidth} bytes", file=sys.stderr)
        sys.exit(1)

    if n_channels > 1:
        audio_i16 = audio_i16.reshape(-1, n_channels).mean(axis=1).astype(np.int16)

    if framerate != TARGET_RATE:
        audio_f32 = int16_to_float32(audio_i16)
        audio_f32 = soxr.resample(audio_f32, framerate, TARGET_RATE)
    else:
        audio_f32 = int16_to_float32(audio_i16)

    duration = len(audio_f32) / TARGET_RATE
    print(f"Transcribing {duration:.1f}s of audio …", file=sys.stderr, flush=True)

    transcriber = Transcriber(model_id=args.model, smooth_with_gemini=args.gemini)
    transcriber.load()

    text = transcriber.transcribe(audio_f32)
    print(text)
