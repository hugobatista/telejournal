#!/bin/bash

# Production Validation Commands for Telegram Journal Bot

set -e

echo "=== Running tests with full coverage enforcement ==="
python -m pytest src tests --cov=src/telejournal --cov-fail-under=100 -v

echo ""
echo "=== Running strict mypy type checks ==="
python -m mypy src --strict

echo ""
echo "=== All validations passed! ==="
