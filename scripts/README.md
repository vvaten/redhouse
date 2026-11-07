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

Install the tracked hook, `scripts/hooks/pre-commit`, once after cloning:

```bash
./deployment/setup-git-hooks.sh
```

It runs the secret checks (see docs/SECURITY.md), then
`run_all_checks.py --quick`. A failure in either blocks the commit. To
commit with failing checks, but still with the secret checks:

```bash
REDHOUSE_ALLOW_FAILING_CHECKS=1 git commit ...
```
