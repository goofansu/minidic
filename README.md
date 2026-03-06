# minidic

A tiny **vibe coding** project for voice dictation on macOS — built as a personal, fast-iteration tool for local use on one machine (not a polished/distributed app).

## Install

```bash
uv tool install --from "git+https://github.com/goofansu/minidic.git" minidic
minidic console
```

The first run will download `mlx-community/parakeet-tdt-0.6b-v3`.

`uv tool` installs `minidic` to `~/.local/bin/minidic`.
Make sure `~/.local/bin` is on your `PATH`.

## Upgrade

```bash
uv tool install --reinstall --from "git+https://github.com/goofansu/minidic.git" minidic
```

## Usage

1. Start menu bar app:

   ```bash
   minidic menubar
   ```

   ![Menu bar icon (stopped)](screenshots/menubar-step1-start.png)
   ![Menu bar icon (running)](screenshots/menubar-step1-status.png)

2. In the menu bar app, click **Start daemon** (or **Stop daemon** to stop it).

3. Press `F5` to toggle start/stop dictation (captured globally; other apps will not receive `F5` while daemon is running).

Other useful commands:

```bash
minidic console
minidic console --gemini
minidic transcribe path/to/file.wav
minidic transcribe --gemini path/to/file.wav
```

## Technique overview

`minidic` captures microphone audio, normalizes it to 16 kHz, and runs local speech-to-text with streaming-style decoding.

High-level pipeline:

1. Capture mic audio with `sounddevice`
2. Resample to 16 kHz with `soxr` (when needed)
3. Transcribe with `parakeet-mlx` on-device (Apple Silicon / MLX stack)
4. Smooth transcription by default with local regex cleanup (remove filler words like `um`, `uh`, etc.)
5. Further smooth with Gemini (`gemini-3.1-flash-lite-preview`, thinking disabled) when `GEMINI_API_KEY` is set and Gemini mode is enabled (via `--gemini` for `console`/`transcribe`, or via the menu bar toggle)
6. Inject text into the active app on macOS

The daemon mode is hotkey-driven and lazily loads/unloads the model to reduce idle resource usage.

Runtime notes:
- macOS permissions (microphone/accessibility) are required.
- Recordings are stored under `~/.minidic/`.
- Logs, PID, and runtime state files are stored under `~/.local/state/minidic/`.
