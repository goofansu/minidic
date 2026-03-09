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
DEFAULT_PROVIDER = "parakeet"
DEFAULT_POLISH_PROVIDER = "none"

ASRProvider = Literal["parakeet", "groq"]
PolishProvider = Literal["none", "groq"]


class Settings(TypedDict):
    asr_provider: ASRProvider
    polish_provider: PolishProvider
    duration_seconds: float


DEFAULT_SETTINGS: Settings = {
    "asr_provider": DEFAULT_PROVIDER,
    "polish_provider": DEFAULT_POLISH_PROVIDER,
    "duration_seconds": DEFAULT_DURATION_SECONDS,
}


def _normalize_asr_provider(value: object, *, default: ASRProvider) -> ASRProvider:
    if value in {"parakeet", "groq"}:
        return cast(ASRProvider, value)
    return default


def _normalize_polish_provider(value: object, *, default: PolishProvider) -> PolishProvider:
    if value in {"none", "groq"}:
        return cast(PolishProvider, value)
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
        "asr_provider": _normalize_asr_provider(
            payload.get("asr_provider"), default=DEFAULT_SETTINGS["asr_provider"]
        ),
        "polish_provider": _normalize_polish_provider(
            payload.get("polish_provider"), default=DEFAULT_SETTINGS["polish_provider"]
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
    return validate_settings(data)


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


def get_asr_provider() -> ASRProvider:
    return read_settings()["asr_provider"]


def set_asr_provider(provider: ASRProvider) -> None:
    settings = read_settings()
    settings["asr_provider"] = _normalize_asr_provider(provider, default=settings["asr_provider"])
    write_settings(settings)


def get_polish_provider() -> PolishProvider:
    return read_settings()["polish_provider"]


def set_polish_provider(provider: PolishProvider) -> None:
    settings = read_settings()
    settings["polish_provider"] = _normalize_polish_provider(
        provider, default=settings["polish_provider"]
    )
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
