#!/bin/bash
# Pre-commit check script
# Runs all linters and tests before allowing commit
# Usage: ./scripts/pre-commit-check.sh

set -e

echo "=== Running pre-commit checks ==="
echo ""

# Activate virtual environment if not already active
if [ -z "$VIRTUAL_ENV" ]; then
    if [ -f "venv/bin/activate" ]; then
        source venv/bin/activate
    elif [ -f "venv/Scripts/activate" ]; then
        source venv/Scripts/activate
    else
        echo "Error: Virtual environment not found"
        exit 1
    fi
fi

# 1. Format check with black
echo "1. Checking code formatting with black..."
black --check --diff src/ tests/ deployment/ || {
    echo ""
    echo "ERROR: Code formatting issues found!"
    echo "Run: black src/ tests/ deployment/"
    exit 1
}
echo "✓ Black formatting OK"
echo ""

# 2. Lint with ruff
echo "2. Linting with ruff..."
ruff check src/ tests/ deployment/ || {
    echo ""
    echo "ERROR: Ruff linting failed!"
    echo "Fix the issues or run: ruff check --fix src/ tests/ deployment/"
    exit 1
}
echo "✓ Ruff linting OK"
echo ""

# 3. Run unit tests
echo "3. Running unit tests..."
pytest tests/unit/ -v --tb=short || {
    echo ""
    echo "ERROR: Unit tests failed!"
    exit 1
}
echo "✓ Unit tests OK"
echo ""

echo "=== All pre-commit checks passed! ==="
echo "You can now commit your changes."
