"""Persistent user settings for minidic."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Literal, Mapping, TypedDict, cast

from minidic.transcribe import DEFAULT_MODEL, GROQ_DEFAULT_MODEL

_SETTINGS_DIR = Path.home() / ".minidic"
SETTINGS_FILE = _SETTINGS_DIR / "settings.json"

DEFAULT_DURATION_SECONDS = 60.0
DEFAULT_PROVIDER = "parakeet"
DEFAULT_ENHANCEMENT_PROVIDER = "none"

ASRProvider = Literal["parakeet", "groq"]
EnhancementProvider = Literal["none", "groq"]


class AsrSettings(TypedDict):
    provider: ASRProvider
    model: str


class EnhancementSettings(TypedDict):
    provider: EnhancementProvider


class RecordingSettings(TypedDict):
    duration_seconds: float


class Settings(TypedDict):
    asr: AsrSettings
    enhancement: EnhancementSettings
    recording: RecordingSettings


DEFAULT_SETTINGS: Settings = {
    "asr": {
        "provider": DEFAULT_PROVIDER,
        "model": DEFAULT_MODEL,
    },
    "enhancement": {
        "provider": DEFAULT_ENHANCEMENT_PROVIDER,
    },
    "recording": {
        "duration_seconds": DEFAULT_DURATION_SECONDS,
    },
}


def _normalize_str(value: object, *, default: str) -> str:
    if isinstance(value, str):
        return value.strip()
    return default


def _normalize_duration_seconds(value: object, *, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        duration = float(value)
        if duration > 0:
            return duration
    return default


def _normalize_asr_provider(value: object, *, default: ASRProvider) -> ASRProvider:
    if value in {"parakeet", "groq"}:
        return cast(ASRProvider, value)
    return default


def _normalize_enhancement_provider(
    value: object, *, default: EnhancementProvider
) -> EnhancementProvider:
    if value in {"none", "groq"}:
        return cast(EnhancementProvider, value)
    return default


def _default_asr_model(provider: ASRProvider) -> str:
    if provider == "groq":
        return GROQ_DEFAULT_MODEL
    return DEFAULT_MODEL


def _validate_asr_settings(data: object, *, default: AsrSettings | None = None) -> AsrSettings:
    fallback = DEFAULT_SETTINGS["asr"] if default is None else default
    payload = data if isinstance(data, Mapping) else {}
    provider = _normalize_asr_provider(payload.get("provider"), default=fallback["provider"])
    default_model = fallback["model"] if provider == fallback["provider"] else _default_asr_model(provider)
    return {
        "provider": provider,
        "model": _normalize_str(payload.get("model"), default=default_model),
    }


def _validate_enhancement_settings(
    data: object, *, default: EnhancementSettings | None = None
) -> EnhancementSettings:
    fallback = DEFAULT_SETTINGS["enhancement"] if default is None else default
    payload = data if isinstance(data, Mapping) else {}
    provider = _normalize_enhancement_provider(
        payload.get("provider"), default=fallback["provider"]
    )
    return {
        "provider": provider,
    }


def _validate_recording_settings(
    data: object, *, default: RecordingSettings | None = None
) -> RecordingSettings:
    fallback = DEFAULT_SETTINGS["recording"] if default is None else default
    payload = data if isinstance(data, Mapping) else {}
    return {
        "duration_seconds": _normalize_duration_seconds(
            payload.get("duration_seconds"),
            default=fallback["duration_seconds"],
        ),
    }


def validate_settings(data: object) -> Settings:
    payload = data if isinstance(data, Mapping) else {}
    return {
        "asr": _validate_asr_settings(payload.get("asr")),
        "enhancement": _validate_enhancement_settings(payload.get("enhancement")),
        "recording": _validate_recording_settings(payload.get("recording")),
    }


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


def get_asr_settings() -> AsrSettings:
    return read_settings()["asr"]


def set_asr_settings(asr: Mapping[str, object]) -> None:
    settings = read_settings()
    settings["asr"] = _validate_asr_settings(asr, default=settings["asr"])
    write_settings(settings)


def get_enhancement_settings() -> EnhancementSettings:
    return read_settings()["enhancement"]


def set_enhancement_settings(enhancement: Mapping[str, object]) -> None:
    settings = read_settings()
    settings["enhancement"] = _validate_enhancement_settings(
        enhancement,
        default=settings["enhancement"],
    )
    write_settings(settings)


def get_recording_settings() -> RecordingSettings:
    return read_settings()["recording"]


def set_recording_settings(recording: Mapping[str, object]) -> None:
    settings = read_settings()
    settings["recording"] = _validate_recording_settings(recording, default=settings["recording"])
    write_settings(settings)


def get_recording_duration(*, default: float = DEFAULT_DURATION_SECONDS) -> float:
    settings = read_settings()
    return _normalize_duration_seconds(
        settings["recording"].get("duration_seconds"),
        default=default,
    )


def set_recording_duration(duration: float) -> None:
    settings = read_settings()
    settings["recording"]["duration_seconds"] = _normalize_duration_seconds(
        duration,
        default=DEFAULT_DURATION_SECONDS,
    )
    write_settings(settings)
