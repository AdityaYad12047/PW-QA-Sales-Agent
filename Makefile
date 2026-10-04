# Makefile for PW Counselling QA
# Run `make help` to see available commands.

.PHONY: help install install-dev test eval run lint clean

help:
	@echo "Available commands:"
	@echo "  make install       Install production dependencies"
	@echo "  make install-dev   Install dev + test dependencies"
	@echo "  make test          Run unit and integration tests (excludes eval)"
	@echo "  make eval          Run evaluation regression tests"
	@echo "  make run           Start the backend dev server"
	@echo "  make lint          Run ruff linter"
	@echo "  make clean         Remove __pycache__ and temp files"

install:
	cd backend && pip install -r requirements.txt

install-dev:
	cd backend && pip install -r requirements-dev.txt

test:
	cd backend && python -m pytest -v -m "not eval"

eval:
	cd backend && python -m pytest -v -m eval
	cd eval && python run_eval.py

run:
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

lint:
	cd backend && python -m ruff check .

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
