.PHONY: install lint test generate pipeline

install:
	pip install -e ".[dev]"

lint:
	ruff check .
	ruff format --check .

test:
	pytest

generate:
	lhc generate

pipeline:
	lhc pipeline
