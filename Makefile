release:
	rm -rf dist
	uv build
	uvx twine check dist/*
	uvx twine upload dist/*
