# Scripts Directory

This directory contains utility scripts for the Redhouse project.

## Quality Check Scripts

### run_all_checks.py (Pre-Commit Script)

Comprehensive quality check script that runs before committing to git.

**Checks performed:**
1. Code formatting (black)
2. Linting (ruff)
3. Type checking (mypy)
4. Unit tests (pytest)
5. Code quality metrics (via code_quality.py)
6. Code coverage (pytest-cov)

**Usage:**
```bash
# Run all checks
python -u scripts/run_all_checks.py

# Quick mode (skip coverage)
python -u scripts/run_all_checks.py --quick

# Auto-fix formatting and linting
python -u scripts/run_all_checks.py --fix

# Skip tests (format/lint only)
python -u scripts/run_all_checks.py --no-tests
```

**When to use:**
- Always run before committing to git
- Run after making code changes
- Run before creating a pull request

**Exit codes:**
- 0: All checks passed
- 1: One or more checks failed

## Test Data Management

### setup_test_buckets.py
Creates InfluxDB test buckets for integration testing.

### clean_test_bucket.py
Cleans test data from InfluxDB test buckets.

### fix_test_data.py
Repairs test data in InfluxDB.

### find_test_data.py
Searches for test data in InfluxDB buckets.

### cleanup_test_data.py
Removes old test data.

## Deployment Scripts

See [deployment/README.md](../deployment/README.md) for deployment-related scripts.

## Pre-Commit Hook

To automatically run checks before every commit, you can install a git pre-commit hook:

```bash
# Create pre-commit hook (Linux/Mac)
cat > .git/hooks/pre-commit << 'EOF'
#!/bin/bash
python -u scripts/run_all_checks.py --quick
EOF
chmod +x .git/hooks/pre-commit

# Create pre-commit hook (Windows Git Bash)
cat > .git/hooks/pre-commit << 'EOF'
#!/bin/bash
python -u scripts/run_all_checks.py --quick
EOF
```

The hook will run automatically before each commit. If checks fail, the commit will be blocked.
