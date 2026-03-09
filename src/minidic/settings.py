"""Persistent user settings for minidic."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Literal, Mapping, TypedDict, cast

_SETTINGS_DIR = Path.home() / ".minidic"
SETTINGS_FILE = _SETTINGS_DIR / "settings.json"

DEFAULT_DURATION_SECONDS = 60.0
DEFAULT_ASR = "offline"
DEFAULT_POLISH = False

ASR = Literal["offline", "groq"]


class Settings(TypedDict):
    asr: ASR
    polish: bool
    duration_seconds: float


DEFAULT_SETTINGS: Settings = {
    "asr": DEFAULT_ASR,
    "polish": DEFAULT_POLISH,
    "duration_seconds": DEFAULT_DURATION_SECONDS,
}


def _normalize_asr(value: object, *, default: ASR) -> ASR:
    if value in {"offline", "groq"}:
        return cast(ASR, value)
    return default


def _normalize_polish(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _normalize_duration_seconds(value: object, *, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        duration = float(value)
        if duration > 0:
            return duration
    return default


def validate_settings(data: object) -> Settings:
    payload = data if isinstance(data, Mapping) else {}
    return {
        "asr": _normalize_asr(payload.get("asr"), default=DEFAULT_SETTINGS["asr"]),
        "polish": _normalize_polish(
            payload.get("polish"),
            default=DEFAULT_SETTINGS["polish"],
        ),
        "duration_seconds": _normalize_duration_seconds(
            payload.get("duration_seconds"), default=DEFAULT_SETTINGS["duration_seconds"]
        ),
    }


def _load_settings_file(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_settings() -> Settings:
    data = _load_settings_file(SETTINGS_FILE)
    if data is None:
        defaults = validate_settings(DEFAULT_SETTINGS)
        if not SETTINGS_FILE.exists():
            write_settings(defaults)
        return defaults
    settings = validate_settings(data)
    if any(data.get(k) != v for k, v in settings.items()):
        write_settings(settings)
    return settings


def write_settings(settings: Mapping[str, object]) -> None:
    _SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    payload = validate_settings(settings)
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"

    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=_SETTINGS_DIR,
            prefix="settings-",
            suffix=".json.tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = temp_file.name
        os.replace(temp_path, SETTINGS_FILE)
    finally:
        if temp_path is not None:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                pass


def get_asr() -> ASR:
    return read_settings()["asr"]


def set_asr(asr: ASR) -> None:
    settings = read_settings()
    settings["asr"] = _normalize_asr(asr, default=settings["asr"])
    write_settings(settings)


def get_polish() -> bool:
    return read_settings()["polish"]


def set_polish(enabled: bool) -> None:
    settings = read_settings()
    settings["polish"] = _normalize_polish(enabled, default=settings["polish"])
    write_settings(settings)


def get_recording_duration(*, default: float = DEFAULT_DURATION_SECONDS) -> float:
    settings = read_settings()
    return _normalize_duration_seconds(settings["duration_seconds"], default=default)


def set_recording_duration(duration: float) -> None:
    settings = read_settings()
    settings["duration_seconds"] = _normalize_duration_seconds(
        duration, default=DEFAULT_DURATION_SECONDS
    )
    write_settings(settings)
