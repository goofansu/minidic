"""Runtime configuration persisted for menubar/daemon coordination."""

from __future__ import annotations

import json
from pathlib import Path

_STATE_DIR = Path.home() / ".local" / "state" / "minidic"
RUNTIME_CONFIG_FILE = _STATE_DIR / "config.json"


DEFAULT_RUNTIME_CONFIG: dict[str, bool] = {
    "gemini": False,
}


def _normalize_bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def read_runtime_config() -> dict[str, bool]:
    config = dict(DEFAULT_RUNTIME_CONFIG)

    try:
        data = json.loads(RUNTIME_CONFIG_FILE.read_text())
    except OSError:
        return config
    except json.JSONDecodeError:
        return config

    if isinstance(data, dict):
        config["gemini"] = _normalize_bool(data.get("gemini"), default=config["gemini"])

    return config


def write_runtime_config(config: dict[str, bool]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
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
