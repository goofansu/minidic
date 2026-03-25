"""Tests for persisted settings validation."""

from __future__ import annotations

import json

from minidic import settings as settings_module
from minidic.settings import (
    DEFAULT_GROQ_WHISPER_PROMPT,
    DEFAULT_HOTKEY,
    DEFAULT_HOTKEY_MODE,
    validate_settings,
)


class TestValidateSettings:
    def test_groq_whisper_prompt_defaults_to_empty_string(self):
        settings = validate_settings({})

        assert settings["groq_whisper_prompt"] == DEFAULT_GROQ_WHISPER_PROMPT

    def test_groq_whisper_prompt_accepts_strings(self):
        settings = validate_settings({"groq_whisper_prompt": "Hello, world!"})

        assert settings["groq_whisper_prompt"] == "Hello, world!"

    def test_groq_whisper_prompt_rejects_non_strings(self):
        settings = validate_settings({"groq_whisper_prompt": 123})

        assert settings["groq_whisper_prompt"] == DEFAULT_GROQ_WHISPER_PROMPT

    def test_hotkey_mode_defaults_to_toggle(self):
        settings = validate_settings({})

        assert settings["hotkey_mode"] == DEFAULT_HOTKEY_MODE

    def test_hotkey_mode_accepts_supported_values_case_insensitively(self):
        settings = validate_settings({"hotkey_mode": "Push_To_Talk"})

        assert settings["hotkey_mode"] == "push_to_talk"

    def test_hotkey_mode_rejects_unknown_values(self):
        settings = validate_settings({"hotkey_mode": "hold"})

        assert settings["hotkey_mode"] == DEFAULT_HOTKEY_MODE

    def test_hotkey_defaults_to_f5(self):
        settings = validate_settings({})

        assert settings["hotkey"] == DEFAULT_HOTKEY

    def test_hotkey_accepts_supported_values_case_insensitively(self):
        settings = validate_settings({"hotkey": "right_command"})

        assert settings["hotkey"] == "RIGHT_COMMAND"

    def test_hotkey_rejects_unknown_values(self):
        settings = validate_settings({"hotkey": "space"})

        assert settings["hotkey"] == DEFAULT_HOTKEY

    def test_read_settings_backfills_new_keys_for_existing_files(self, tmp_path, monkeypatch):
        settings_dir = tmp_path / ".minidic"
        settings_file = settings_dir / "settings.json"
        settings_dir.mkdir()
        settings_file.write_text(
            json.dumps(
                {
                    "online": True,
                    "polish": True,
                    "duration_seconds": 12,
                    "groq_whisper_prompt": "prompt",
                }
            )
        )

        monkeypatch.setattr(settings_module, "_SETTINGS_DIR", settings_dir)
        monkeypatch.setattr(settings_module, "SETTINGS_FILE", settings_file)

        settings = settings_module.read_settings()

        assert settings["hotkey"] == DEFAULT_HOTKEY
        assert settings["hotkey_mode"] == DEFAULT_HOTKEY_MODE

        persisted = json.loads(settings_file.read_text())
        assert persisted["hotkey"] == DEFAULT_HOTKEY
        assert persisted["hotkey_mode"] == DEFAULT_HOTKEY_MODE
