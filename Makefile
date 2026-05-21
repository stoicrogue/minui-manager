# Cross-platform-ish Makefile. On Windows, prefer `pwsh ./make.ps1 <target>`.
# Targets here assume a POSIX-ish shell.

.PHONY: install run dev build test fmt lint

install:
	python -m venv .venv
	. .venv/bin/activate && pip install -e ".[dev]"
	cd frontend && npm install

run: frontend/dist/minui-manager-ui/browser/index.html
	. .venv/bin/activate && uvicorn app.main:app --app-dir backend --port 8000

frontend/dist/minui-manager-ui/browser/index.html:
	cd frontend && npx ng build

build:
	cd frontend && npx ng build

dev:
	. .venv/bin/activate && uvicorn app.main:app --reload --app-dir backend --port 8000

test:
	. .venv/bin/activate && pytest

fmt:
	. .venv/bin/activate && ruff format backend

lint:
	. .venv/bin/activate && ruff check backend
