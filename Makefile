install:
	uv tool install --reinstall --from . minidic

release:
	rm -rf dist
	uv build
	uvx twine check dist/*
	uvx twine upload dist/*
