{
  "id": "e0abf66e",
  "title": "Step 1: Project setup — uv init, add dependencies, verify permissions",
  "tags": [
    "implementation",
    "setup"
  ],
  "status": "done",
  "created_at": "2026-03-01T08:49:57.392Z"
}

- `uv init --lib` — created src layout with `pyproject.toml`
- `uv add parakeet-mlx sounddevice silero-vad pyobjc-framework-Quartz` — all installed successfully
- System deps: `portaudio` available via nix home-manager, `ffmpeg` already installed
- Verified `sounddevice.query_devices()` — detects MacBook Pro Microphone (input) and speakers (output)
- Verified `Quartz.CGEventCreateKeyboardEvent` import — OK (runtime accessibility permission needed for actual injection)
- Created file structure: `src/minidic/{__init__,main,audio,vad,transcribe,inject}.py`
