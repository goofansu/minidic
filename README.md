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

`uv tool` installs `minidic` to `~/.local/bin/minidic`.
Make sure `~/.local/bin` is on your `PATH`.

## Usage

On first use, macOS will prompt for the permissions required by `minidic`. In general, you need to grant these permissions to the terminal app you use to run `minidic`:

- **Microphone** — needed to capture live audio for dictation
- **Accessibility** — needed to inject transcribed text into the active app and handle global hotkeys in menu bar mode

Environment variables:

- `GROQ_API_KEY` — required when using `--online` or `--polish`

### Console

Run an interactive dictation session in the terminal. It records from your microphone, transcribes speech, and prints the result.

By default, `minidic` uses the offline Parakeet backend for local transcription. On first use it will download `mlx-community/parakeet-tdt-0.6b-v3` (~2 GB).

```bash
minidic console
minidic console --online
minidic console --polish
minidic console --online --polish
```

`--online` switches to Groq Whisper. `--polish` enables the built-in Groq polish backend for punctuation and phrasing cleanup after transcription.

### Transcribe

Transcribe an existing WAV file from disk instead of recording live microphone input.

```bash
minidic transcribe path/to/file.wav
minidic transcribe --online path/to/file.wav
minidic transcribe --polish path/to/file.wav
minidic transcribe --online --polish path/to/file.wav
```

### Menu bar

Run `minidic` in menu bar mode with a background daemon and a global `F5` hotkey to toggle dictation.

```bash
minidic menubar
```

![Menu bar icon (stopped)](https://raw.githubusercontent.com/goofansu/minidic/main/screenshots/menubar-daemon-stopped.png)
![Menu bar icon (running)](https://raw.githubusercontent.com/goofansu/minidic/main/screenshots/menubar-daemon-started.png)

The `menubar` command itself does not accept online or polish flags. Use the menu bar UI to change backend, polish, and duration.

The menu bar UI lets you change settings without restarting the daemon; changes apply on the next transcription:

1. Start menu bar mode.
2. Optionally choose **Backend**: `Offline (Parakeet)` or `Online (Groq Whisper)`.
3. Optionally choose **Polish**: `No` or `Yes`.
4. Optionally choose a max recording length from **Duration**.
5. Click **Start daemon**.
6. Press `F5` to toggle start/stop dictation.

Groq features require `GROQ_API_KEY`. If the key is missing, `minidic` raises an error; in daemon mode, the error is logged to `daemon.log`.

## How it works

`minidic` captures microphone audio, normalizes it to 16 kHz, and runs speech-to-text plus optional cleanup.

### Models used

- **Offline ASR:** `mlx-community/parakeet-tdt-0.6b-v3`
- **Online ASR:** Groq `whisper-large-v3-turbo`
- **Polish model:** Groq `openai/gpt-oss-20b`

### High-level pipeline

1. Capture mic audio with `sounddevice`
2. Resample to 16 kHz with `soxr` when needed
3. Transcribe with Parakeet or Groq depending on whether `online` is enabled
4. Apply local regex cleanup by default to remove filler words like `um` and `uh`
5. Optionally run Groq polish when enabled
6. Inject text into the active app on macOS in daemon mode

The daemon mode is hotkey-driven and lazily loads and unloads the ASR model to reduce idle resource usage.

### Directory structure

```text
~/.minidic/
├── settings.json          # persisted settings for online mode, polish, and recording duration
└── recordings/            # WAV recordings created during dictation/transcription

~/.local/state/minidic/
├── daemon.error           # last daemon error message (transient)
├── daemon.log             # daemon logs
├── daemon.pid             # daemon process ID
├── daemon.state           # current daemon state: idle, recording, transcribing, error
├── menubar.log            # menu bar mode logs
└── menubar.pid            # menu bar process ID
```

### Configuration

`minidic` stores persistent configuration in `~/.minidic/settings.json` as a flat JSON object with these keys:

- `online`: `true` or `false`
- `polish`: `true` or `false`
- `duration_seconds`

Default `settings.json`:

```json
{
  "duration_seconds": 60.0,
  "online": false,
  "polish": false
}
```
