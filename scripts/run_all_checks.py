#!/usr/bin/env python3
"""
Comprehensive pre-commit quality check script.

Runs all quality checks before committing to git:
- Code formatting (black)
- Linting (ruff)
- Type checking (mypy)
- Unit tests (pytest)
- Code quality metrics
- Code coverage

Usage:
    python scripts/run_all_checks.py              # Run all checks
    python scripts/run_all_checks.py --quick      # Skip coverage (faster)
    python scripts/run_all_checks.py --fix        # Auto-fix formatting and linting
"""

import argparse
import subprocess
import sys
from pathlib import Path

OK = "[OK]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"


def print_header(text: str):
    """Print a section header."""
    print(f"\n{'='*70}")
    print(f"{text}")
    print(f"{'='*70}\n")


def run_command(cmd: list, cwd: Path, timeout: int = 120) -> tuple[bool, str]:
    """Run a command and return success status and output."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
        output = result.stdout + result.stderr
        passed = result.returncode == 0
        return passed, output
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout}s"
    except Exception as e:
        return False, f"Command failed: {e}"


def check_formatting(root_dir: Path, fix: bool = False) -> bool:
    """Check code formatting with black."""
    print_header("1. CODE FORMATTING (black)")

    targets = ["src/", "tests/", "deployment/"]
    cmd = [sys.executable, "-m", "black"]
    if not fix:
        cmd.extend(["--check", "--diff"])
    cmd.extend(targets)

    passed, output = run_command(cmd, root_dir)

    if passed:
        print(f"{OK} Black formatting check passed")
        return True
    else:
        print(f"{FAIL} Black formatting issues found")
        if not fix:
            print("\nTo fix, run: black src/ tests/ deployment/")
            # Show first 20 lines of diff
            lines = [ln for ln in output.split("\n") if ln][:20]
            for line in lines:
                print(f"  {line}")
        else:
            print(f"{OK} Auto-fixed formatting issues (re-run to verify)")
        return False


def check_linting(root_dir: Path, fix: bool = False) -> bool:
    """Check linting with ruff."""
    print_header("2. LINTING (ruff)")

    cmd = [sys.executable, "-m", "ruff", "check"]
    if fix:
        cmd.append("--fix")
    cmd.append(".")

    passed, output = run_command(cmd, root_dir)

    if passed:
        print(f"{OK} Ruff linting passed")
        return True
    else:
        # Count errors
        error_count = len(
            [
                line
                for line in output.strip().split("\n")
                if line and ":" in line and not line.startswith("Found")
            ]
        )
        print(f"{FAIL} Ruff found {error_count} issues")
        if not fix:
            print("\nTo fix, run: ruff check --fix .")
            # Show first 20 errors
            lines = [ln for ln in output.strip().split("\n") if ln][:20]
            for line in lines:
                print(f"  {line}")
            if error_count > 20:
                print(f"  ... and {error_count - 20} more")
        else:
            print(f"{OK} Auto-fixed linting issues (re-run to verify)")
        return False


def check_types(root_dir: Path) -> bool:
    """Check type hints with mypy."""
    print_header("3. TYPE CHECKING (mypy)")

    cmd = [sys.executable, "-m", "mypy", "src/", "--exclude", "src/tools"]
    passed, output = run_command(cmd, root_dir)

    # Count actual errors (not warnings)
    error_count = len([line for line in output.strip().split("\n") if ": error:" in line])

    if error_count == 0:
        print(f"{OK} Mypy type checking passed (0 errors)")
        return True
    else:
        print(f"{FAIL} Mypy found {error_count} type errors")
        # Show first 20 errors
        lines = [ln for ln in output.strip().split("\n") if ": error:" in ln][:20]
        for line in lines:
            print(f"  {line}")
        if error_count > 20:
            print(f"  ... and {error_count - 20} more")
        return False


def run_tests(root_dir: Path) -> bool:
    """Run unit tests with pytest."""
    print_header("4. UNIT TESTS (pytest)")

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit/",
        "-v",
        "--tb=short",
        "-q",  # Quieter output
    ]
    passed, output = run_command(cmd, root_dir, timeout=180)

    if passed:
        # Extract test counts from output
        lines = output.strip().split("\n")
        summary_line = [ln for ln in lines if "passed" in ln.lower()]
        if summary_line:
            print(f"{OK} Unit tests passed: {summary_line[-1].strip()}")
        else:
            print(f"{OK} Unit tests passed")
        return True
    else:
        print(f"{FAIL} Unit tests failed")
        # Show failure summary
        lines = output.strip().split("\n")
        print("\nFailure summary:")
        for line in lines[-30:]:  # Last 30 lines usually have the summary
            print(f"  {line}")
        return False


def check_code_quality(root_dir: Path) -> bool:
    """Check code quality metrics."""
    print_header("5. CODE QUALITY METRICS")

    cmd = [sys.executable, "code_quality.py", "--check"]
    passed, output = run_command(cmd, root_dir, timeout=60)

    if passed:
        print(f"{OK} Code quality metrics within limits")
    else:
        print(f"{FAIL} Hard limit violations found (blocking)")

    # Always show the summary
    lines = output.strip().split("\n")
    for line in lines:
        print(f"  {line}")

    return passed


def check_coverage(root_dir: Path) -> bool:
    """Check code coverage."""
    print_header("6. CODE COVERAGE")

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/",
        "-q",
        "--cov=src",
        "--cov-report=term",
        "--cov-config=.coveragerc",
    ]
    passed, output = run_command(cmd, root_dir, timeout=180)

    # Parse coverage output - find the table section
    lines = output.split("\n")
    coverage_started = False
    coverage_lines = []
    total_line = None

    for line in lines:
        # Start capturing after the header line
        if "Name" in line and "Stmts" in line and "Miss" in line:
            coverage_started = True
            coverage_lines.append(line)
            continue

        if coverage_started:
            # Stop at separator line after TOTAL
            if line.startswith("-" * 20) and total_line:
                break
            # Capture coverage lines
            if line.strip() and not line.startswith("="):
                coverage_lines.append(line)
                if "TOTAL" in line:
                    total_line = line

    if not total_line:
        print(f"{FAIL} Could not parse coverage report")
        return False

    # Display per-file coverage
    print(f"{OK} Coverage report:")
    for line in coverage_lines:
        print(f"  {line}")

    # Check if below threshold (warning, not failure)
    if "%" in total_line:
        pct_str = total_line.split()[-1].replace("%", "")
        try:
            coverage_pct = int(pct_str)
            if coverage_pct < 85:
                print(f"\n[WARN] Coverage is {coverage_pct}% (recommended: 85%+)")
        except ValueError:
            pass

    return True  # Don't fail on coverage, just warn


def main():
    parser = argparse.ArgumentParser(description="Run all pre-commit quality checks")
    parser.add_argument("--quick", action="store_true", help="Skip coverage check (faster)")
    parser.add_argument("--fix", action="store_true", help="Auto-fix formatting and linting issues")
    parser.add_argument(
        "--no-tests", action="store_true", help="Skip running tests (format/lint only)"
    )
    args = parser.parse_args()

    root_dir = Path(__file__).parent.parent

    print("\nRunning pre-commit quality checks...")
    print(f"Working directory: {root_dir}")

    results = {}

    # 1. Code formatting
    results["formatting"] = check_formatting(root_dir, fix=args.fix)

    # 2. Linting
    results["linting"] = check_linting(root_dir, fix=args.fix)

    # 3. Type checking
    results["types"] = check_types(root_dir)

    # 4. Unit tests
    if not args.no_tests:
        results["tests"] = run_tests(root_dir)
    else:
        print_header("4. UNIT TESTS (pytest)")
        print(f"{SKIP} Skipped (--no-tests)")
        results["tests"] = True

    # 5. Code quality
    results["quality"] = check_code_quality(root_dir)

    # 6. Code coverage
    if not args.quick and not args.no_tests:
        results["coverage"] = check_coverage(root_dir)
    else:
        print_header("6. CODE COVERAGE")
        print(f"{SKIP} Skipped (--quick or --no-tests)")
        results["coverage"] = True

    # Final summary
    print_header("SUMMARY")

    passed_count = sum(1 for v in results.values() if v)
    total_count = len(results)

    for check, passed in results.items():
        status = OK if passed else FAIL
        print(f"  {status} {check.capitalize()}")

    print()
    if passed_count == total_count:
        print(f"[SUCCESS] All checks passed! " f"({passed_count}/{total_count})")
        print("\nYou can now commit your changes.")
        return 0
    else:
        failed_count = total_count - passed_count
        print(f"[FAILED] {failed_count} check(s) failed " f"({passed_count}/{total_count} passed)")
        if args.fix:
            print("\nSome issues were auto-fixed. " "Re-run to verify.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
