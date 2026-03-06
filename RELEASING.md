# Releasing `minidic`

## One-time PyPI setup

This project uses **manual publishing with a PyPI API token**.

Before publishing, confirm:

- the PyPI project name `minidic` is available
- the package metadata is correct
- you are happy with the version in `pyproject.toml`
- your PyPI API token is ready

## Build and validate locally

Use `uv` from the repo root:

```bash
uv build
uvx twine check dist/*
```

## Optional: test upload to TestPyPI

```bash
export TWINE_USERNAME=__token__
export TWINE_PASSWORD=<your-testpypi-token>
uvx twine upload --repository testpypi dist/*
```

Then test install:

```bash
uv tool install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple minidic
```

## Publish to PyPI

```bash
export TWINE_USERNAME=__token__
export TWINE_PASSWORD=<your-pypi-token>
uvx twine upload dist/*
```

## Post-release smoke test

```bash
uv tool install --refresh minidic
minidic --help
```

## Notes

- This project is macOS-only in practice.
- The built distributions are created in `dist/`.
- Bump the version in `pyproject.toml` before each release.
