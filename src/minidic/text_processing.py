"""Common transcript text processing utilities."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

GROQ_POLISH_MODEL = "llama-3.1-8b-instant"

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


class GroqSmoother:
    """Optional transcript post-processor backed by a small Groq LLM."""

    def __init__(self) -> None:
        self._api_key = os.environ.get("GROQ_API_KEY", "").strip()
        self._client: Any | None = None
        self._disabled = False

        if not self._api_key:
            self._disabled = True
            return

        try:
            from groq import Groq as _Groq
        except Exception:
            logger.warning(
                "GROQ_API_KEY is set but groq is not installed; skipping transcript smoothing."
            )
            self._disabled = True
            return

        self._client = _Groq(api_key=self._api_key)
        logger.info("Groq transcript smoothing enabled (%s).", GROQ_POLISH_MODEL)

    @property
    def enabled(self) -> bool:
        return not self._disabled and self._client is not None

    def smooth(self, text: str) -> str:
        if not self.enabled or not text.strip():
            return text

        assert self._client is not None

        try:
            response = self._client.chat.completions.create(
                model=GROQ_POLISH_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You clean up raw speech-to-text output for dictation. "
                            "Preserve original meaning and language. "
                            "Fix punctuation/casing and smooth awkward phrasing. "
                            "Do not add new facts. "
                            "Return only the final rewritten text."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                max_tokens=1024,
                temperature=0,
            )
            smoothed = (response.choices[0].message.content or "").strip()
        except Exception:
            logger.warning("Groq smoothing failed; using raw transcript.", exc_info=True)
            return text

        if smoothed:
            return smoothed

        logger.warning("Groq smoothing returned empty text; using raw transcript.")
        return text
