# Cross-platform-ish Makefile. On Windows, prefer `pwsh ./make.ps1 <target>`.
# Targets here assume a POSIX-ish shell.

.PHONY: install dev test fmt lint

install:
	python -m venv .venv
	. .venv/bin/activate && pip install -e ".[dev]"

dev:
	. .venv/bin/activate && uvicorn app.main:app --reload --app-dir backend --port 8000

test:
	. .venv/bin/activate && pytest

fmt:
	. .venv/bin/activate && ruff format backend

lint:
	. .venv/bin/activate && ruff check backend
