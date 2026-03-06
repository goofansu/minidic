# minidic

A tiny **vibe coding** project for voice dictation on macOS — built as a personal, fast-iteration tool for local use on one machine (not a polished/distributed app).

## Install

```bash
uv tool install --from "git+https://github.com/goofansu/minidic.git" minidic
```

To upgrade an existing install:

```bash
uv tool install --reinstall --from "git+https://github.com/goofansu/minidic.git" minidic
```

The first run will download `mlx-community/parakeet-tdt-0.6b-v3`.

`uv tool` installs `minidic` to `~/.local/bin/minidic`.
Make sure `~/.local/bin` is on your `PATH`.

## Usage

### Console

Run an interactive dictation session in the terminal. This records from your microphone, transcribes locally, and inserts the final text into the active app.

```bash
minidic console
minidic console --gemini
```

### Transcribe

Transcribe an existing audio file from disk instead of recording live microphone input.

```bash
minidic transcribe path/to/file.wav
minidic transcribe --gemini path/to/file.wav
```

### Menubar

Run `minidic` as a menu bar app with a background daemon and global `F5` hotkey for push-to-toggle dictation.

```bash
minidic menubar
```

![Menu bar icon (stopped)](screenshots/menubar-daemon-stopped.png)
![Menu bar icon (running)](screenshots/menubar-daemon-started.png)

1. Start the menu bar app.
2. In the menu bar app, click **Start daemon** (or **Stop daemon** to stop it).
3. Press `F5` to toggle start/stop dictation (captured globally; other apps will not receive `F5` while daemon is running).

## Technique overview

`minidic` captures microphone audio, normalizes it to 16 kHz, and runs local speech-to-text with streaming-style decoding.

### Models used

- **ASR model:** `parakeet-mlx` for on-device audio transcription on Apple Silicon / MLX
- **LLM model:** `gemini-3.1-flash-lite-preview` for optional transcript cleanup (thinking disabled)

### High-level pipeline

1. Capture mic audio with `sounddevice`
2. Resample to 16 kHz with `soxr` (when needed)
3. Transcribe with `parakeet-mlx` on-device
4. Smooth transcription by default with local regex cleanup (remove filler words like `um`, `uh`, etc.)
5. Further smooth with Gemini when `GEMINI_API_KEY` is set and Gemini mode is enabled (via `--gemini` for `console`/`transcribe`, or via the menu bar toggle)
6. Inject text into the active app on macOS

The daemon mode is hotkey-driven and lazily loads/unloads the model to reduce idle resource usage.

### Directory structure

- `~/.minidic/` — user data directory
- `~/.minidic/recordings/` — saved WAV recordings captured during dictation/transcription
- `~/.local/state/minidic/` — runtime state directory
- `~/.local/state/minidic/daemon.log` — daemon logs
- `~/.local/state/minidic/menubar.log` — menu bar app logs
- `~/.local/state/minidic/daemon.pid` — daemon process ID
- `~/.local/state/minidic/menubar.pid` — menu bar process ID
- `~/.local/state/minidic/daemon.state` — current daemon state (`idle`, `recording`, `transcribing`)
- `~/.local/state/minidic/config.json` — persisted runtime config such as Gemini toggle state

Runtime notes:
- macOS permissions (microphone/accessibility) are required.
- Recordings are stored under `~/.minidic/`.
- Logs, PID, and runtime state files are stored under `~/.local/state/minidic/`.
