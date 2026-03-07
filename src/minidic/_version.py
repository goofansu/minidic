"""Version string for minidic."""

from __future__ import annotations

import subprocess
from importlib.metadata import version as _pkg_version


def _git_short_hash() -> str | None:
    """Return the short git commit hash, or None if unavailable."""
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        return None


def version_string() -> str:
    """Return ``1.0.5 (abc1234)`` in dev, ``1.0.5`` in release."""
    ver = _pkg_version("minidic")
    commit = _git_short_hash()
    if commit:
        return f"{ver} ({commit})"
    return ver
