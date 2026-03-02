"""Entry point and command dispatcher for minidic."""

from __future__ import annotations

import argparse

from minidic.handlers import (
    cmd_daemon_foreground,
    cmd_menubar,
    cmd_menubar_foreground,
    cmd_start,
    cmd_status,
    cmd_stop,
    cmd_transcribe,
    run_interactive,
)
from minidic.transcribe import DEFAULT_MODEL


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="minidic",
        description="Real-time voice dictation for macOS",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    p.add_argument("--model", default=DEFAULT_MODEL, help="HuggingFace model id (default: %(default)s)")
    p.add_argument("--duration", type=float, default=60, help="Max recording duration in seconds (default: 60)")

    sub = p.add_subparsers(dest="command")
    sub.add_parser("start", help="Start the dictation daemon in the background")
    sub.add_parser("stop", help="Stop the running daemon")
    sub.add_parser("status", help="Show daemon status")

    sp_transcribe = sub.add_parser("transcribe", help="Transcribe a WAV file")
    sp_transcribe.add_argument("file", help="Path to WAV file")

    sub.add_parser("menubar", help="Launch menu bar status app in background")
    sub.add_parser("_menubar")
    sub.add_parser("_daemon")
    return p.parse_args(argv)


def main() -> None:
    args = parse_args()
    dispatch = {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "transcribe": cmd_transcribe,
        "menubar": cmd_menubar,
        "_menubar": cmd_menubar_foreground,
        "_daemon": cmd_daemon_foreground,
        None: run_interactive,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
