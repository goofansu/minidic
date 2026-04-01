# CLAUDE.md

This file provides guidance for AI assistants working on the minidic codebase.

## Project Overview

**minidic** is a lightweight macOS dictation tool that captures audio and transcribes it via speech-to-text. It supports two ASR backends:
- **Offline**: `parakeet-mlx` (local model, Apple Silicon only, ~2 GB)
- **Online**: Groq Whisper API (requires `GROQ_API_KEY`)

It runs as a terminal CLI, a menu bar daemon, or a background process triggered by a global hotkey.

## Development Environment

**Package manager**: `uv` — use it for all Python commands.

```bash
uv run pytest               # run tests
uv run python -m minidic    # run the tool
uv build                    # build distribution
```

Never use `pip` directly. Never use `python` without `uv run` unless in an activated virtual environment.

**Python version**: 3.12+ (see `.python-version`)

**PYTHONPATH**: Set to `src/` when running tests manually:
```bash
PYTHONPATH=src uv run pytest tests/ -v
```

## Repository Structure

```
src/minidic/          # Main package
  main.py             # CLI entry point and argument parsing
  handlers.py         # Top-level command handlers (console, transcribe, menubar, daemon)
  transcribe.py       # ASR abstraction (Transcriber facade, Parakeet + Groq backends)
  audio.py            # Microphone capture via sounddevice + soxr resampling
  text_processing.py  # Filler word removal (regex) + optional Groq LLM polish
  settings.py         # Persistent JSON settings (~/.minidic/settings.json)
  hotkey.py           # macOS global hotkey listener via CGEventTap
  inject.py           # Text injection via clipboard + Cmd+V simulation
  menubar.py          # AppKit-based macOS menu bar UI
  daemon.py           # Hotkey-driven dictation daemon lifecycle
  runtime/
    process.py        # PID files, lock files, subprocess spawning
    state.py          # Transient state files in ~/.local/state/minidic/
tests/
  conftest.py         # Mocks for heavy/platform deps (mlx, Quartz, sounddevice, soxr)
  test_*.py           # Test modules per source file
```

## Running Tests

```bash
make test             # Equivalent to: uv run pytest
```

Tests run on Linux (no macOS required) because `conftest.py` mocks all platform-specific dependencies. Always verify tests pass before committing.

## System Dependencies

- Check whether command-line tools exist using `which` before assuming they are available.
- **Require user confirmation before installing any system dependencies.**

## Key Architecture Patterns

### ASR Abstraction (transcribe.py)
`Transcriber` is a facade over `_LocalTranscriber` (Parakeet/MLX) and `_GroqTranscriber` (Groq API). Both inherit from `_BaseTranscriber`, which applies the text cleaning pipeline (regex smoother → optional Groq polish) after transcription.

### Daemon Lifecycle (daemon.py)
The daemon loads the ASR model lazily on first use and unloads it after 30 minutes of idle time. State is tracked (idle / recording / transcribing / error) and written to `~/.local/state/minidic/daemon.state`.

### Process Coordination (runtime/)
The daemon and menu bar processes are mutually exclusive via lock files. PID files allow health checks. `runtime/process.py` handles spawning detached subprocesses and managing these files.

### Settings (settings.py)
Persistent settings are stored as JSON at `~/.minidic/settings.json`. Each setting has a dedicated getter/setter with validation/normalization. Settings are written atomically (temp file + rename).

### Hotkeys (hotkey.py)
`GlobalHotkeyListener` uses macOS `CGEventTap` (Quartz) to intercept keys globally before apps receive them. Supported hotkeys: F1–F12 and modifier keys (Right Command, Option, Shift, Control). Two modes: `toggle` and `push_to_talk`.

### Text Injection (inject.py)
`inject_text()` copies text to the clipboard and simulates Cmd+V to paste at the cursor. This is the mechanism for inserting transcribed text into any active app.

## Runtime State Directories

| Path | Contents |
|------|----------|
| `~/.minidic/` | `settings.json`, `recordings/` |
| `~/.local/state/minidic/` | `daemon.pid`, `menubar.pid`, `daemon.log`, `daemon.error`, `daemon.state`, `menubar.lock` |

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `GROQ_API_KEY` | Required for Groq Whisper ASR and/or Groq LLM polish |
| `HF_HUB_OFFLINE` | Set internally when attempting offline Parakeet model load |
| `PYTHONPATH` | Set to `src/` in CI and for manual test runs |

## Release Process

1. Update version in `pyproject.toml` and `src/minidic/_version.py` (keep in sync).
2. Run `uv lock` to update `uv.lock`.
3. Commit with message: `chore: release vX.Y.Z`
4. Run `make release` — cleans `dist/`, runs `uv build`, checks with `twine`, uploads to PyPI.

## Commit Style

Use conventional commits:
- `feat:` new feature
- `fix:` bug fix
- `chore:` maintenance (version bumps, dependency updates)
- `docs:` documentation only
- `refactor:` code restructuring without behavior change

## CI/CD

- **test.yml**: Runs `pytest` on Ubuntu with Python 3.12. Does NOT run on macOS.
- **publish.yml**: Publishes to PyPI on tagged releases.

CI sets `PYTHONPATH=src` and installs `pytest numpy groq` directly (no heavy platform deps needed because of mocks).

## Code Style Notes

- Use `from __future__ import annotations` for deferred type evaluation.
- Heavy/platform-specific imports are done lazily inside functions (e.g., `from minidic.menubar import run_menubar` inside handlers) to keep startup fast and tests mockable.
- Per-module loggers: `logger = logging.getLogger(__name__)`.
- Use context managers for resource cleanup (`AudioStream`, `StreamSession`).
- Threading: hotkey listener runs on a daemon thread with a `CFRunLoop`; audio chunks pass via `queue.Queue`.
