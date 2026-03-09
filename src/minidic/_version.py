"""Version string for minidic."""

from __future__ import annotations

import subprocess
from importlib.metadata import version as _pkg_version
from pathlib import Path


_PACKAGE_ROOT = Path(__file__).resolve().parent
__version__ = "1.2.3"


def _git_short_hash() -> str | None:
    """Return the short git commit hash for the package checkout, or None."""
    try:
        return (
            subprocess.check_output(
                ["git", "-C", str(_PACKAGE_ROOT), "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        return None


def version_string() -> str:
    try:
        ver = _pkg_version("minidic")
    except Exception:
        ver = __version__
    commit = _git_short_hash()
    if commit:
        return f"{ver} ({commit})"
    return ver
