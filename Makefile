.PHONY: install lint test generate

install:
	pip install -e ".[dev]"

lint:
	ruff check .
	ruff format --check .

test:
	pytest

generate:
	lhc generate
