"""Persistent user settings for minidic."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Final, Mapping, TypedDict

_SETTINGS_DIR = Path.home() / ".minidic"
SETTINGS_FILE = _SETTINGS_DIR / "settings.json"

DEFAULT_DURATION_SECONDS = 60.0
DEFAULT_ONLINE = False
DEFAULT_POLISH = False
DEFAULT_GROQ_WHISPER_PROMPT = ""
DEFAULT_HOTKEY = "F5"
DEFAULT_HOTKEY_MODE = "toggle"
DEFAULT_VAD_SILENCE_DURATION = 1.5
_VAD_SILENCE_MIN = 0.3
_VAD_SILENCE_MAX = 10.0
SUPPORTED_HOTKEY_MODES: Final[tuple[str, ...]] = ("toggle", "push_to_talk")
SUPPORTED_HOTKEYS: Final[tuple[str, ...]] = (
    "F1",
    "F2",
    "F3",
    "F4",
    "F5",
    "F6",
    "F7",
    "F8",
    "F9",
    "F10",
    "F11",
    "F12",
    "RIGHT_COMMAND",
    "RIGHT_OPTION",
    "RIGHT_SHIFT",
    "RIGHT_CONTROL",
)


class Settings(TypedDict):
    online: bool
    polish: bool
    duration_seconds: float
    groq_whisper_prompt: str
    hotkey: str
    hotkey_mode: str
    vad_silence_duration: float


DEFAULT_SETTINGS: Settings = {
    "online": DEFAULT_ONLINE,
    "polish": DEFAULT_POLISH,
    "duration_seconds": DEFAULT_DURATION_SECONDS,
    "groq_whisper_prompt": DEFAULT_GROQ_WHISPER_PROMPT,
    "hotkey": DEFAULT_HOTKEY,
    "hotkey_mode": DEFAULT_HOTKEY_MODE,
    "vad_silence_duration": DEFAULT_VAD_SILENCE_DURATION,
}


def _normalize_bool(value: object, *, default: bool) -> bool:
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


def _normalize_text(value: object, *, default: str) -> str:
    if isinstance(value, str):
        return value
    return default


def _normalize_hotkey_mode(value: object, *, default: str) -> str:
    if not isinstance(value, str):
        return default
    normalized = value.strip().lower()
    if normalized in SUPPORTED_HOTKEY_MODES:
        return normalized
    return default


def _normalize_vad_silence_duration(value: object, *, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        duration = float(value)
        if _VAD_SILENCE_MIN <= duration <= _VAD_SILENCE_MAX:
            return duration
    return default


def _normalize_hotkey(value: object, *, default: str) -> str:
    if not isinstance(value, str):
        return default
    normalized = value.strip().upper()
    if normalized in SUPPORTED_HOTKEYS:
        return normalized
    return default


def validate_settings(data: object) -> Settings:
    payload = data if isinstance(data, Mapping) else {}
    return {
        "online": _normalize_bool(payload.get("online"), default=DEFAULT_SETTINGS["online"]),
        "polish": _normalize_bool(payload.get("polish"), default=DEFAULT_SETTINGS["polish"]),
        "duration_seconds": _normalize_duration_seconds(
            payload.get("duration_seconds"), default=DEFAULT_SETTINGS["duration_seconds"]
        ),
        "groq_whisper_prompt": _normalize_text(
            payload.get("groq_whisper_prompt"),
            default=DEFAULT_SETTINGS["groq_whisper_prompt"],
        ),
        "hotkey": _normalize_hotkey(
            payload.get("hotkey"),
            default=DEFAULT_SETTINGS["hotkey"],
        ),
        "hotkey_mode": _normalize_hotkey_mode(
            payload.get("hotkey_mode"),
            default=DEFAULT_SETTINGS["hotkey_mode"],
        ),
        "vad_silence_duration": _normalize_vad_silence_duration(
            payload.get("vad_silence_duration"),
            default=DEFAULT_SETTINGS["vad_silence_duration"],
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


def get_online() -> bool:
    return read_settings()["online"]


def get_provider() -> str:
    return "whisper" if get_online() else "parakeet"


def set_online(enabled: bool) -> None:
    settings = read_settings()
    settings["online"] = _normalize_bool(enabled, default=settings["online"])
    write_settings(settings)


def get_polish() -> bool:
    return read_settings()["polish"]


def set_polish(enabled: bool) -> None:
    settings = read_settings()
    settings["polish"] = _normalize_bool(enabled, default=settings["polish"])
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


def get_groq_whisper_prompt() -> str:
    return read_settings()["groq_whisper_prompt"]


def set_groq_whisper_prompt(prompt: str) -> None:
    settings = read_settings()
    settings["groq_whisper_prompt"] = _normalize_text(
        prompt, default=DEFAULT_GROQ_WHISPER_PROMPT
    )
    write_settings(settings)


def get_hotkey() -> str:
    return read_settings()["hotkey"]


def set_hotkey(hotkey: str) -> None:
    settings = read_settings()
    settings["hotkey"] = _normalize_hotkey(hotkey, default=DEFAULT_HOTKEY)
    write_settings(settings)


def get_hotkey_mode() -> str:
    return read_settings()["hotkey_mode"]


def set_hotkey_mode(mode: str) -> None:
    settings = read_settings()
    settings["hotkey_mode"] = _normalize_hotkey_mode(mode, default=DEFAULT_HOTKEY_MODE)
    write_settings(settings)


def get_vad_silence_duration() -> float:
    return read_settings()["vad_silence_duration"]


def set_vad_silence_duration(duration: float) -> None:
    settings = read_settings()
    settings["vad_silence_duration"] = _normalize_vad_silence_duration(
        duration, default=DEFAULT_VAD_SILENCE_DURATION
    )
    write_settings(settings)
