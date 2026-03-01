"""Entry point for minidic — macOS voice dictation."""

from __future__ import annotations

import argparse
import logging
import os
import queue as _queue
import signal
import subprocess as _subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

from minidic.audio import AudioStream, TARGET_RATE, int16_to_float32
from minidic.inject import inject_text
from minidic.transcribe import DEFAULT_MODEL, Transcriber


logger = logging.getLogger(__name__)

_MINIDIC_DIR = Path.home() / ".minidic"
_PID_FILE = _MINIDIC_DIR / "daemon.pid"
_LOG_FILE = _MINIDIC_DIR / "daemon.log"
_MENUBAR_PID_FILE = _MINIDIC_DIR / "menubar.pid"
_MENUBAR_LOG_FILE = _MINIDIC_DIR / "menubar.log"
_MODEL_IDLE_UNLOAD_SECONDS = 5 * 60


def _save_wav(chunks: list[np.ndarray]) -> Path:
    """Save chunks as 16-bit PCM mono WAV under ~/.minidic/recordings/<unix-ts>.wav."""
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


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="minidic",
        description="Real-time voice dictation for macOS",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    p.add_argument(
        "--model", default=DEFAULT_MODEL,
        help="HuggingFace model id (default: %(default)s)",
    )
    p.add_argument(
        "--duration", type=float, default=60,
        help="Max recording duration in seconds (default: 60)",
    )

    sub = p.add_subparsers(dest="command")

    # --- start ---
    sub.add_parser("start", help="Start the dictation daemon in the background")

    # --- stop ---
    sub.add_parser("stop", help="Stop the running daemon")

    # --- status ---
    sub.add_parser("status", help="Show daemon status")

    # --- transcribe ---
    sp_transcribe = sub.add_parser("transcribe", help="Transcribe a WAV file")
    sp_transcribe.add_argument("file", help="Path to WAV file")

    # --- menubar ---
    sub.add_parser(
        "menubar",
        help="Launch menu bar status app in background",
    )

    # --- _menubar (hidden, used internally by menubar) ---
    sub.add_parser("_menubar")

    # --- _daemon (hidden, used internally by start) ---
    sub.add_parser("_daemon")

    return p.parse_args(argv)


def _setup_logging(verbose: bool, *, to_file: bool = False) -> None:
    kwargs: dict = dict(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    if to_file:
        _MINIDIC_DIR.mkdir(parents=True, exist_ok=True)
        kwargs["filename"] = str(_LOG_FILE)
        kwargs["filemode"] = "w"
    logging.basicConfig(**kwargs)


# ---------------------------------------------------------------------------
# Interactive mode (default, no subcommand)
# ---------------------------------------------------------------------------

def run_interactive(args: argparse.Namespace) -> None:
    """Interactive dictation loop — record and display transcription.

    Flow:
    1. Press Enter to start recording
    2. Record until Ctrl+C or max duration
    3. Transcribe and display the result (no text injection)
    """
    _setup_logging(args.verbose)

    model = args.model
    duration = args.duration

    transcriber = Transcriber(model_id=model)
    print(f"Loading ASR model ({model}) …", flush=True)
    transcriber.load()
    print("ASR model ready.", flush=True)

    max_speech_samples = int(duration * TARGET_RATE)

    print(f"Ready. Enter to record, Ctrl+C to stop.", flush=True)

    try:
        while True:
            sys.stdin.readline()
            print("\033[A\033[K", end="", flush=True)  # erase the blank line

            chunks: list[np.ndarray] = []
            sample_count = 0

            try:
                with AudioStream() as audio:
                    while sample_count < max_speech_samples:
                        chunk = audio.read(timeout=2.0)
                        chunks.append(chunk)
                        sample_count += len(chunk)
                        elapsed = sample_count / TARGET_RATE
                        print(
                            f"\r\033[K🎤 {elapsed:.1f}s",
                            end="", flush=True,
                        )
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


# ---------------------------------------------------------------------------
# Daemon management
# ---------------------------------------------------------------------------

def _is_minidic_process(pid: int) -> bool:
    """Return True if *pid* belongs to a running minidic daemon."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # Verify the command line matches the exact invocation we use to
    # spawn the daemon ("-m minidic" + "_daemon") so we never target
    # an unrelated process that happens to reuse the PID.
    try:
        out = _subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="],
            text=True,
            stderr=_subprocess.DEVNULL,
        ).strip()
        return "-m minidic" in out and "_daemon" in out
    except (OSError, _subprocess.CalledProcessError):
        # If `ps` fails we can't be sure — treat as stale.
        return False


def _read_pid() -> int | None:
    """Read the PID from the pid file, return None if missing or stale."""
    if not _PID_FILE.exists():
        return None
    try:
        pid = int(_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None
    if not _is_minidic_process(pid):
        _PID_FILE.unlink(missing_ok=True)
        return None
    return pid


def _is_menubar_process(pid: int) -> bool:
    """Return True if *pid* belongs to a running minidic menu bar app."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        out = _subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="],
            text=True,
            stderr=_subprocess.DEVNULL,
        ).strip()
        return "-m minidic" in out and "_menubar" in out
    except (OSError, _subprocess.CalledProcessError):
        return False


def _read_menubar_pid() -> int | None:
    """Read the menubar PID from file, return None if missing or stale."""
    if not _MENUBAR_PID_FILE.exists():
        return None
    try:
        pid = int(_MENUBAR_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None
    if not _is_menubar_process(pid):
        _MENUBAR_PID_FILE.unlink(missing_ok=True)
        return None
    return pid


def cmd_start(args: argparse.Namespace) -> None:
    """Start the dictation daemon in the background."""
    import subprocess

    existing = _read_pid()
    if existing is not None:
        print(f"Daemon already running (pid {existing}).", flush=True)
        sys.exit(1)

    _MINIDIC_DIR.mkdir(parents=True, exist_ok=True)

    # Build command for the hidden _daemon subcommand.
    # Global options (--verbose, --model, --duration)
    # must precede the subcommand.
    cmd = [sys.executable, "-m", "minidic"]
    if args.verbose:
        cmd.append("--verbose")
    cmd.extend(["--model", args.model, "--duration", str(args.duration)])
    cmd.append("_daemon")

    # Spawn a fresh process (avoids fork-in-multithreaded crash on macOS).
    devnull = open(os.devnull, "r+b")
    proc = subprocess.Popen(
        cmd,
        stdin=devnull,
        stdout=devnull,
        stderr=devnull,
        start_new_session=True,
    )

    # Poll until the daemon signals readiness (PID file) or dies.
    # The daemon writes the PID file after initialization succeeds
    # (hotkey listener active), so its appearance is a reliable
    # readiness signal.
    start_time = time.monotonic()
    notified = False
    while True:
        rc = proc.poll()
        if rc is not None:
            print(
                f"Daemon failed to start (exit code {rc}). "
                f"Check log: {_LOG_FILE}",
                flush=True,
            )
            sys.exit(1)
        if _PID_FILE.exists():
            print(
                f"Daemon started (pid {proc.pid}). Log: {_LOG_FILE}",
                flush=True,
            )
            return
        if not notified and time.monotonic() - start_time > 3:
            print(
                "Waiting for daemon to initialize …",
                flush=True,
            )
            notified = True
        time.sleep(0.2)


def cmd_daemon(args: argparse.Namespace) -> None:
    """Run the daemon in the foreground (called by cmd_start).

    The PID file is written by ``_run_daemon`` *after* initialization
    succeeds (hotkey listener active). ``cmd_start`` polls for its
    appearance as the readiness signal.
    """
    _MINIDIC_DIR.mkdir(parents=True, exist_ok=True)
    _setup_logging(args.verbose, to_file=True)

    try:
        _run_daemon(args)
    except Exception:
        logger.exception("Daemon crashed")
        sys.exit(1)
    finally:
        _PID_FILE.unlink(missing_ok=True)


def cmd_stop(args: argparse.Namespace) -> None:
    """Stop the running daemon."""
    pid = _read_pid()
    if pid is None:
        print("No daemon to stop.", flush=True)
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        # Daemon exited between _read_pid() and kill — treat as stopped.
        _PID_FILE.unlink(missing_ok=True)
        print(f"Daemon already exited (pid {pid}).", flush=True)
        return

    # Wait for process to exit.
    alive = True
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except OSError:
            alive = False
            break
        time.sleep(0.1)

    if alive:
        print(f"Daemon (pid {pid}) did not exit within 5s.", flush=True)
        sys.exit(1)

    _PID_FILE.unlink(missing_ok=True)
    print(f"Daemon stopped (pid {pid}).", flush=True)


def cmd_status(args: argparse.Namespace) -> None:
    """Show daemon status."""
    pid = _read_pid()
    if pid is None:
        print("Daemon is not running.", flush=True)
    else:
        print(f"Daemon is running (pid {pid}).", flush=True)


# ---------------------------------------------------------------------------
# Global hotkey daemon
# ---------------------------------------------------------------------------

def _run_daemon(args: argparse.Namespace) -> None:
    """Global-hotkey dictation daemon (runs in background).

    The microphone is only opened while recording (between hotkey presses)
    to avoid holding the device and wasting resources when idle.  A
    short post-roll drain ensures the tail of speech isn't clipped.
    """
    from minidic.hotkey import GlobalHotkeyListener

    # Handle SIGTERM for clean shutdown.
    shutdown = threading.Event()

    def _on_sigterm(signum: int, frame: object) -> None:
        shutdown.set()

    signal.signal(signal.SIGTERM, _on_sigterm)

    # --- Initialize models (lazy-load on first transcription) ---
    transcriber = Transcriber(model_id=args.model)
    model_loaded = False
    last_model_use: float | None = None
    model_lock = threading.Lock()
    logger.info("ASR model will load on first transcription (%s).", args.model)

    max_speech_samples = int(args.duration * TARGET_RATE)

    # Mic is opened on-demand — no persistent audio stream.
    audio: AudioStream | None = None
    recording_chunks: list[np.ndarray] = []
    sample_count = 0
    mode = "idle"  # "idle" | "recording" | "draining" | "transcribing"
    lock = threading.Lock()

    # Signal the audio-pump to finish recording and hand off chunks.
    finish_event = threading.Event()

    # -- audio pump --------------------------------------------------------
    # The audio-pump is the *sole* consumer of audio.queue, which avoids
    # race conditions from multiple threads pulling from the same queue.

    def _audio_pump() -> None:
        nonlocal sample_count, mode
        while not shutdown.is_set():
            with lock:
                current_audio = audio
            if current_audio is None:
                # No mic open — sleep briefly and re-check.
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
                    # Tail chunk captured during drain window.
                    recording_chunks.append(chunk)

            # Handle finish outside the lock to avoid holding it
            # during thread spawn / transcription handoff.
            if finish_event.is_set():
                finish_event.clear()
                threading.Thread(
                    target=_finish_recording,
                    name="finish-rec",
                    daemon=True,
                ).start()

    # -- recording control -------------------------------------------------

    def _finish_recording() -> None:
        nonlocal sample_count, mode, audio

        # Brief drain: switch to "draining" so the audio-pump still
        # appends tail chunks, then collect them.
        with lock:
            if mode != "recording":
                return  # Already finished (race between max-duration and F5).
            mode = "draining"

        # Give the audio callback a moment to push any buffered data.
        time.sleep(0.05)

        with lock:
            mode = "transcribing"
            chunks = list(recording_chunks)
            recording_chunks.clear()
            sample_count = 0
            # Close the mic — no longer needed until next F5.
            if audio is not None:
                audio.stop()
                audio = None
            logger.info("Recording stopped (mic closed).")

        threading.Thread(
            target=_transcribe_and_inject, args=(chunks,),
            name="transcriber", daemon=True,
        ).start()

    def _transcribe_and_inject(chunks: list[np.ndarray]) -> None:
        nonlocal mode, model_loaded, last_model_use
        try:
            if not chunks:
                return

            wav_path = _save_wav(chunks)
            logger.info("Saved %s", wav_path)

            audio_f32 = int16_to_float32(np.concatenate(chunks))
            duration = len(audio_f32) / TARGET_RATE
            logger.info("Transcribing %.1fs …", duration)

            with model_lock:
                if not model_loaded:
                    logger.info("Loading ASR model (%s) …", args.model)
                    transcriber.load()
                    model_loaded = True
                    logger.info("ASR model ready.")

                text = transcriber.transcribe(audio_f32)
                last_model_use = time.monotonic()

            if text.strip():
                inject_text(text)
                logger.info("Injected: %s", text)
            else:
                logger.info("No speech detected.")
        except Exception:
            logger.exception("Transcription/injection error")
        finally:
            with lock:
                mode = "idle"

    # -- model lifecycle ---------------------------------------------------

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
                logger.info(
                    "ASR model unloaded after %.0fs idle.",
                    idle_for,
                )

    # -- hotkey handler ----------------------------------------------------

    def on_hotkey() -> None:
        nonlocal sample_count, mode, audio

        with lock:
            if mode == "idle":
                recording_chunks.clear()
                sample_count = 0
                # Open mic on demand — only enter "recording" on success.
                try:
                    stream = AudioStream()
                    stream.start()
                except Exception:
                    logger.exception("Failed to open microphone")
                    return
                audio = stream
                mode = "recording"
                logger.info("Recording started (mic opened).")

            elif mode == "recording":
                # Signal the audio-pump to finish (it owns the queue).
                finish_event.set()

            else:
                logger.debug("Hotkey ignored — transcription in progress")

    # --- Start threads ----------------------------------------------------
    threading.Thread(target=_audio_pump, name="audio-pump", daemon=True).start()
    threading.Thread(target=_model_reaper, name="model-reaper", daemon=True).start()

    listener = GlobalHotkeyListener(on_hotkey=on_hotkey)
    listener.start()

    # All initialization succeeded — publish PID file so cmd_start and
    # cmd_status can discover us.  This is the readiness signal.
    _PID_FILE.write_text(str(os.getpid()))
    logger.info("Daemon ready — F5 to dictate.")

    # Block until SIGTERM.
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


# ---------------------------------------------------------------------------
# Menu bar app
# ---------------------------------------------------------------------------

def cmd_menubar(args: argparse.Namespace) -> None:
    """Launch the menu bar app in background and return immediately."""
    import subprocess

    existing = _read_menubar_pid()
    if existing is not None:
        print(f"Menu bar app already running (pid {existing}).", flush=True)
        sys.exit(1)

    _MINIDIC_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, "-m", "minidic"]
    if args.verbose:
        cmd.append("--verbose")
    cmd.extend(
        [
            "--model",
            args.model,
            "--duration",
            str(args.duration),
            "_menubar",
        ]
    )

    devnull = open(os.devnull, "r+b")
    with _MENUBAR_LOG_FILE.open("a", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            cmd,
            stdin=devnull,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )

    # Wait briefly for startup signal (pid file) or immediate crash.
    start_time = time.monotonic()
    while time.monotonic() - start_time < 2.0:
        rc = proc.poll()
        if rc is not None:
            print(
                f"Menu bar app failed to start (exit code {rc}). "
                f"Check log: {_MENUBAR_LOG_FILE}",
                flush=True,
            )
            sys.exit(1)

        running_pid = _read_menubar_pid()
        if running_pid is not None:
            print(f"Menu bar app launched (pid {running_pid}).", flush=True)
            return

        time.sleep(0.1)

    rc = proc.poll()
    if rc is not None:
        print(
            f"Menu bar app failed to start (exit code {rc}). "
            f"Check log: {_MENUBAR_LOG_FILE}",
            flush=True,
        )
        sys.exit(1)

    proc.terminate()
    print(
        f"Menu bar app launch timed out before readiness signal. "
        f"Check log: {_MENUBAR_LOG_FILE}",
        flush=True,
    )
    sys.exit(1)


def cmd_menubar_foreground(args: argparse.Namespace) -> None:
    """Run the menu bar app in foreground (internal)."""
    from minidic.menubar import run_menubar

    existing = _read_menubar_pid()
    if existing is not None and existing != os.getpid():
        print(f"Menu bar app already running (pid {existing}).", flush=True)
        sys.exit(1)

    _MINIDIC_DIR.mkdir(parents=True, exist_ok=True)
    _MENUBAR_PID_FILE.write_text(str(os.getpid()))
    try:
        run_menubar(args)
    finally:
        _MENUBAR_PID_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Transcribe file
# ---------------------------------------------------------------------------

def cmd_transcribe(args: argparse.Namespace) -> None:
    """Transcribe a WAV file and print the result to stdout."""
    import soxr

    _setup_logging(args.verbose)

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
        wav_path.name, n_channels, sampwidth * 8, framerate,
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

    transcriber = Transcriber(model_id=args.model)
    transcriber.load()

    text = transcriber.transcribe(audio_f32)
    print(text)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    match args.command:
        case "start":
            cmd_start(args)
        case "stop":
            cmd_stop(args)
        case "status":
            cmd_status(args)
        case "transcribe":
            cmd_transcribe(args)
        case "menubar":
            cmd_menubar(args)
        case "_menubar":
            cmd_menubar_foreground(args)
        case "_daemon":
            cmd_daemon(args)
        case _:
            run_interactive(args)


if __name__ == "__main__":
    main()
