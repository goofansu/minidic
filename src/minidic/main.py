"""Entry point and command dispatcher for minidic."""

from __future__ import annotations

import argparse

from minidic._version import version_string
from minidic.handlers import (
    cmd_daemon_foreground,
    cmd_menubar,
    cmd_menubar_foreground,
    cmd_transcribe,
    run_interactive,
)
from minidic.settings import (
    DEFAULT_DURATION_SECONDS,
    DEFAULT_ENHANCEMENT_PROVIDER,
    DEFAULT_PROVIDER,
)


def _add_common_options(parser: argparse.ArgumentParser, *, include_duration: bool) -> None:
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--provider",
        choices=("parakeet", "groq"),
        default=DEFAULT_PROVIDER,
        help="ASR backend provider (default: parakeet)",
    )
    parser.add_argument(
        "--enhancement",
        choices=("none", "groq"),
        default=DEFAULT_ENHANCEMENT_PROVIDER,
        help="Transcript enhancement provider (default: none)",
    )
    if include_duration:
        parser.add_argument(
            "--duration",
            type=float,
            default=DEFAULT_DURATION_SECONDS,
            help=f"Max recording duration in seconds (default: {DEFAULT_DURATION_SECONDS:g})",
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="minidic",
        description="Tiny macOS dictation tool on your menubar",
    )
    p.add_argument("--version", action="version", version=version_string())

    sub = p.add_subparsers(dest="command", metavar="{console,menubar,transcribe}")

    sp_console = sub.add_parser("console", help="Run interactive console dictation")
    _add_common_options(sp_console, include_duration=True)

    sp_menubar = sub.add_parser("menubar", help="Launch menu bar status app in background")
    sp_menubar.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    sp_menubar.set_defaults(
        provider=DEFAULT_PROVIDER,
        enhancement=DEFAULT_ENHANCEMENT_PROVIDER,
        duration=DEFAULT_DURATION_SECONDS,
    )

    sp_transcribe = sub.add_parser("transcribe", help="Transcribe a WAV file")
    _add_common_options(sp_transcribe, include_duration=False)
    sp_transcribe.add_argument("file", help="Path to WAV file")

    sp_menubar_fg = sub.add_parser("_menubar")
    _add_common_options(sp_menubar_fg, include_duration=True)

    sp_daemon_fg = sub.add_parser("_daemon")
    _add_common_options(sp_daemon_fg, include_duration=True)

    sub._choices_actions = [a for a in sub._choices_actions if not a.dest.startswith("_")]
    return p.parse_args(argv)


def main() -> None:
    args = parse_args()
    dispatch = {
        "console": run_interactive,
        "transcribe": cmd_transcribe,
        "menubar": cmd_menubar,
        "_menubar": cmd_menubar_foreground,
        "_daemon": cmd_daemon_foreground,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        return

    handler(args)


if __name__ == "__main__":
    main()
