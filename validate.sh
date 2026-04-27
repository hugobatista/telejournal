#!/bin/bash

# Production Validation Commands for Telegram Journal Bot

set -e

echo "=== Running full validation pipeline ==="
hatch run validate

echo ""
echo "=== All validations passed! ==="
