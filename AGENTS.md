# AGENTS.md

## Code style
- Use uv to run python commands.

## System dependencies
- Check whether command line tools exist using the which command.
- Require my confirmation before installing any system dependencies.

## Release
1. Update version in `pyproject.toml` and `_version.py`.
2. Run the `uv lock` command to update the version in `uv.lock`.
