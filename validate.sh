#!/bin/bash

# Production Validation Commands for Telegram Journal Bot

set -e

echo "=== Running tests with full coverage enforcement ==="
python -m pytest src tests --cov=src/telegram_journal_bot --cov-fail-under=100 -v

echo ""
echo "=== Running strict mypy type checks ==="
python -m mypy src tests --strict

echo ""
echo "=== All validations passed! ==="
