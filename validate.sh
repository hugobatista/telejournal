#!/bin/bash

# Production Validation Commands for Telegram Journal Bot

set -e

echo "=== Running linters ==="
uv run ruff check . --fix
uv run black .


echo "=== Running tests with full coverage enforcement ==="
uv run pytest src tests --cov=src/telejournal --cov-fail-under=100 -v

echo ""
echo "=== Running strict mypy type checks ==="
uv run mypy src --strict --no-incremental

echo ""
echo "=== All validations passed! ==="
