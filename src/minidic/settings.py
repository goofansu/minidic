"""Persistent user settings for minidic."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_SETTINGS_DIR = Path.home() / ".minidic"
SETTINGS_FILE = _SETTINGS_DIR / "settings.json"

DEFAULT_DURATION_SECONDS = 60.0

DEFAULT_SETTINGS: dict[str, Any] = {
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


def _load_settings_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except OSError:
        return None
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    return data


def read_settings() -> dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)

    data = _load_settings_file(SETTINGS_FILE)
    if data is None:
        return settings

    settings["gemini"] = _normalize_bool(data.get("gemini"), default=settings["gemini"])
    settings["duration"] = _normalize_duration(data.get("duration"), default=settings["duration"])
    return settings


def write_settings(settings: dict[str, Any]) -> None:
    _SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "duration": _normalize_duration(
            settings.get("duration"), default=DEFAULT_DURATION_SECONDS
        ),
        "gemini": _normalize_bool(settings.get("gemini"), default=False),
    }
    SETTINGS_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def get_gemini_enabled(*, default: bool = False) -> bool:
    settings = read_settings()
    return _normalize_bool(settings.get("gemini"), default=default)


def set_gemini_enabled(enabled: bool) -> None:
    settings = read_settings()
    settings["gemini"] = bool(enabled)
    write_settings(settings)


def get_recording_duration(*, default: float = DEFAULT_DURATION_SECONDS) -> float:
    settings = read_settings()
    return _normalize_duration(settings.get("duration"), default=default)


def set_recording_duration(duration: float) -> None:
    settings = read_settings()
    settings["duration"] = _normalize_duration(duration, default=DEFAULT_DURATION_SECONDS)
    write_settings(settings)
