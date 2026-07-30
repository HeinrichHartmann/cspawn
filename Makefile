.PHONY: install lint push

install:
	uv tool install -e .

lint:
	uv run --with ruff ruff check --fix src/

push:
	git push origin main
