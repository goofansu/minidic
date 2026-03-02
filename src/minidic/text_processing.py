"""Common transcript text processing utilities."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.5-flash"

# Filler words / hesitation sounds to strip from transcription output.
# Matched case-insensitively as whole words.
FILLER_WORDS = frozenset({
    "um", "uh", "uhh", "umm", "erm", "er", "ah", "ahh",
    "hm", "hmm", "huh", "mm", "mmm", "mhm",
})

_FILLER_PATTERN = re.compile(
    r"\b("
    + "|".join(re.escape(w) for w in sorted(FILLER_WORDS, key=len, reverse=True))
    + r")\b[,;]?\s*",
    re.IGNORECASE,
)


class RegexSmoother:
    """Local regex-based transcript cleaner."""

    @property
    def enabled(self) -> bool:
        return True

    def smooth(self, text: str) -> str:
        # Remove filler words along with any trailing comma/semicolon
        cleaned = _FILLER_PATTERN.sub(" ", text)
        # Collapse runs of whitespace
        cleaned = re.sub(r"  +", " ", cleaned)
        # Remove leading comma/semicolon (if filler was at sentence start)
        cleaned = re.sub(r"^\s*[,;]\s*", "", cleaned)
        return cleaned.strip()


def remove_fillers(text: str) -> str:
    """Backward-compatible helper for filler removal."""
    return RegexSmoother().smooth(text)


class GeminiSmoother:
    """Optional transcript post-processor backed by Gemini."""

    def __init__(self) -> None:
        self._api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        self._client: Any | None = None
        self._disabled = False

        if not self._api_key:
            self._disabled = True
            return

        try:
            from google import genai as _genai
        except Exception:
            logger.warning(
                "GEMINI_API_KEY is set but google-genai is not installed; skipping transcript smoothing."
            )
            self._disabled = True
            return

        self._client = _genai.Client(api_key=self._api_key)
        logger.info("Gemini transcript smoothing enabled (%s).", GEMINI_MODEL)

    @property
    def enabled(self) -> bool:
        return not self._disabled and self._client is not None

    def smooth(self, text: str) -> str:
        if not self.enabled or not text.strip():
            return text

        assert self._client is not None

        prompt = (
            "You clean up raw speech-to-text output for dictation. "
            "Preserve original meaning and language. "
            "Fix punctuation/casing and smooth awkward phrasing. "
            "Do not add new facts. "
            "Return only the final rewritten text.\n\n"
            f"Transcript:\n{text}"
        )

        try:
            response = self._client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={"thinking_config": {"thinking_budget": 0}},
            )
        except Exception:
            logger.exception("Gemini smoothing failed; using raw transcript.")
            return text

        smoothed = (getattr(response, "text", "") or "").strip()
        if smoothed:
            return smoothed

        logger.warning("Gemini smoothing returned empty text; using raw transcript.")
        return text
