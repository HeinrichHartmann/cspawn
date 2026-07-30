.PHONY: install lint push test

install:
	uv tool install -e .

lint:
	uv run --with ruff ruff check --fix src/

test:
	uv run --extra dev pytest tests/ -v

push:
	git push origin main
