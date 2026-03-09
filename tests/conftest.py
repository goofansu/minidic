"""Mock heavy platform-specific dependencies before any project imports.

mlx / parakeet_mlx are Apple Silicon only; Quartz / AppKit / Foundation are
macOS-only; sounddevice / soxr require audio hardware / native libs.
Patching sys.modules here lets the test suite run on any platform (Linux, CI).
"""

import sys
from unittest.mock import MagicMock

for _mod in [
    "mlx",
    "mlx.core",
    "parakeet_mlx",
    "sounddevice",
    "soxr",
    "Quartz",
    "AppKit",
    "Foundation",
]:
    sys.modules.setdefault(_mod, MagicMock())
