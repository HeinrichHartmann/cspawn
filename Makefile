.PHONY: install lint

install:
	uv tool install -e .

lint:
	uv run --with ruff ruff check --fix src/
