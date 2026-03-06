"""Runtime configuration persisted for menubar/daemon coordination."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_STATE_DIR = Path.home() / ".local" / "state" / "minidic"
RUNTIME_CONFIG_FILE = _STATE_DIR / "config.json"

DEFAULT_DURATION_SECONDS = 60.0

DEFAULT_RUNTIME_CONFIG: dict[str, Any] = {
    "gemini": False,
    "duration": DEFAULT_DURATION_SECONDS,
}


def _normalize_bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _normalize_duration(value: object, *, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        duration = float(value)
        if duration > 0:
            return duration
    return default


def read_runtime_config() -> dict[str, Any]:
    config = dict(DEFAULT_RUNTIME_CONFIG)

    try:
        data = json.loads(RUNTIME_CONFIG_FILE.read_text())
    except OSError:
        return config
    except json.JSONDecodeError:
        return config

    if isinstance(data, dict):
        config["gemini"] = _normalize_bool(data.get("gemini"), default=config["gemini"])
        config["duration"] = _normalize_duration(data.get("duration"), default=config["duration"])

    return config


def write_runtime_config(config: dict[str, Any]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "duration": _normalize_duration(
            config.get("duration"), default=DEFAULT_DURATION_SECONDS
        ),
        "gemini": _normalize_bool(config.get("gemini"), default=False),
    }
    RUNTIME_CONFIG_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def get_gemini_enabled(*, default: bool = False) -> bool:
    cfg = read_runtime_config()
    return _normalize_bool(cfg.get("gemini"), default=default)


def set_gemini_enabled(enabled: bool) -> None:
    cfg = read_runtime_config()
    cfg["gemini"] = bool(enabled)
    write_runtime_config(cfg)


def get_recording_duration(*, default: float = DEFAULT_DURATION_SECONDS) -> float:
    cfg = read_runtime_config()
    return _normalize_duration(cfg.get("duration"), default=default)


def set_recording_duration(duration: float) -> None:
    cfg = read_runtime_config()
    cfg["duration"] = _normalize_duration(duration, default=DEFAULT_DURATION_SECONDS)
    write_runtime_config(cfg)
