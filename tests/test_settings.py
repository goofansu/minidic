"""Tests for persisted settings validation."""

from minidic.settings import DEFAULT_GROQ_WHISPER_PROMPT, validate_settings


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
