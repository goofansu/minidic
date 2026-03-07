# minidic

A tiny macOS dictation tool for fast voice input from the menu bar or terminal.

## Install

`minidic` is published on PyPI for macOS users.

```bash
uv tool install minidic
```

To upgrade an existing install:

```bash
uv tool upgrade minidic
```

The first time you use the default offline backend, `minidic` will download `mlx-community/parakeet-tdt-0.6b-v3`.

`uv tool` installs `minidic` to `~/.local/bin/minidic`.
Make sure `~/.local/bin` is on your `PATH`.

## Usage

On first use, macOS will prompt for the permissions required by `minidic`. In general, you need to grant these permissions to the terminal app you use to run `minidic`:

- **Microphone** — needed to capture live audio for dictation
- **Accessibility** — needed to inject transcribed text into the active app and handle global hotkeys in menu bar mode

Environment variables:

- `GROQ_API_KEY` — required when using `--provider groq`
- `GEMINI_API_KEY` — required when using `--enhancement gemini`

### Console

Run an interactive dictation session in the terminal. It records from your microphone, transcribes speech, and prints the result.

```bash
minidic console
minidic console --provider groq
minidic console --enhancement gemini
```

Use Parakeet for fully local transcription or Groq for cloud-based transcription. Gemini is optional and can improve punctuation and phrasing after transcription. You can combine `--provider groq` with `--enhancement gemini`.

### Transcribe

Transcribe an existing WAV file from disk instead of recording live microphone input.

```bash
minidic transcribe path/to/file.wav
minidic transcribe --provider groq path/to/file.wav
minidic transcribe --enhancement gemini path/to/file.wav
```

### Menu bar

Run `minidic` in menu bar mode with a background daemon and a global `F5` hotkey to toggle dictation.

```bash
minidic menubar
```

![Menu bar icon (stopped)](https://raw.githubusercontent.com/goofansu/minidic/main/screenshots/menubar-daemon-stopped.png)
![Menu bar icon (running)](https://raw.githubusercontent.com/goofansu/minidic/main/screenshots/menubar-daemon-started.png)

The `menubar` command itself does not accept ASR or enhancement selection flags. Use the menu bar UI to change ASR, enhancement, and duration.

The menu bar UI lets you change settings without restarting the daemon; changes apply on the next transcription:

1. Start menu bar mode.
2. Optionally choose **ASR**: `Offline (Parakeet)` or `Online (Groq)`.
3. Optionally choose **Enhancement**: `None` or `Gemini`.
4. Optionally choose a max recording length from **Duration**.
5. Click **Start daemon**.
6. Press `F5` to toggle start/stop dictation.

Groq and Gemini require their respective API keys. If a key is missing, `minidic` raises an error; in daemon mode, the error is logged to `daemon.log`.

## How it works

`minidic` captures microphone audio, normalizes it to 16 kHz, and runs speech-to-text plus optional cleanup.

### Models used

- **Offline ASR:** `parakeet-mlx` on Apple Silicon via MLX
- **Online ASR:** Groq Whisper (`whisper-large-v3-turbo`)
- **Enhancement model:** `gemini-3.1-flash-lite-preview`

### High-level pipeline

1. Capture mic audio with `sounddevice`
2. Resample to 16 kHz with `soxr` when needed
3. Transcribe with Parakeet or Groq depending on `asr.provider`
4. Apply local regex cleanup by default to remove filler words like `um` and `uh`
5. Optionally run Gemini enhancement when enabled
6. Inject text into the active app on macOS in daemon mode

The daemon mode is hotkey-driven and lazily loads and unloads the ASR model to reduce idle resource usage.

### Directory structure

```text
~/.minidic/
├── settings.json          # persisted settings for `asr`, `enhancement`, and `recording`
└── recordings/            # WAV recordings created during dictation/transcription

~/.local/state/minidic/
├── daemon.log             # daemon logs
├── daemon.pid             # daemon process ID
├── daemon.state           # current daemon state: idle, recording, transcribing
├── menubar.log            # menu bar mode logs
└── menubar.pid            # menu bar process ID
```

### Configuration

`minidic` stores persistent configuration in `~/.minidic/settings.json`.

`minidic` uses three config groups:

- `asr`
  - `provider`: `parakeet` or `groq`
  - `model`: provider-specific internal default; not exposed in the CLI or menu bar UI
- `enhancement`
  - `provider`: `none` or `gemini`
- `recording`
  - `duration_seconds`

Default `settings.json`:

```json
{
  "asr": {
    "model": "mlx-community/parakeet-tdt-0.6b-v3",
    "provider": "parakeet"
  },
  "enhancement": {
    "provider": "none"
  },
  "recording": {
    "duration_seconds": 60.0
  }
}
```
