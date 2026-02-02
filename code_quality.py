"""
Code quality metrics for the Redhouse project.

Measures:
- Lines per file (limit: 500)
- Lines per function (limit: 50)
- Cyclomatic complexity (limit: 10)
- Test coverage report
- Ruff linting (PEP8, naming, mutable defaults)
- Mypy type checking

Usage:
    python code_quality.py
    python code_quality.py --check  # Exit with error if limits exceeded
    python code_quality.py --verbose
    python code_quality.py --lint   # Run ruff + mypy checks
    python code_quality.py --all    # Run all checks including lint and coverage
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Add src to path for quality module imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from quality.analyzers import ProjectMetrics, analyze_file
from quality.report import FAIL, LIMITS, OK, print_report

# Files to analyze (exclude third-party, build artifacts, test files)
EXCLUDE_DIRS = {
    "venv",
    "build",
    "__pycache__",
    ".git",
    "reports",
    ".pytest_cache",
    "old scripts",
    "old backups",
    "wibatemp",
    "scripts",
    "tests",
    ".fissio",
}
EXCLUDE_FILES = {"__init__.py", "emeters_5min_legacy.py"}


def find_python_files(root_dir: Path) -> list[Path]:
    """Find all Python files to analyze."""
    files = []
    for path in root_dir.rglob("*.py"):
        parts = path.relative_to(root_dir).parts
        if any(part in EXCLUDE_DIRS for part in parts):
            continue
        if path.name in EXCLUDE_FILES:
            continue
        files.append(path)
    return sorted(files)


def _check_violations(metrics: ProjectMetrics, root_dir: Path):
    """Check for limit violations and warnings, and add to metrics."""
    for fm in metrics.files:
        rel_path = Path(fm.path).relative_to(root_dir)

        # Check file size limits
        if fm.total_lines > LIMITS["lines_per_file_hard"]:
            metrics.violations.append(
                f"FILE TOO LONG: {rel_path} has {fm.total_lines} lines "
                f"(hard limit: {LIMITS['lines_per_file_hard']})"
            )
        elif fm.total_lines > LIMITS["lines_per_file_soft"]:
            metrics.warnings.append(
                f"FILE TOO LONG: {rel_path} has {fm.total_lines} lines "
                f"(soft limit: {LIMITS['lines_per_file_soft']})"
            )

        # Check function limits
        for func in fm.functions:
            # Function length checks
            if func.lines > LIMITS["lines_per_function_hard"]:
                metrics.violations.append(
                    f"FUNCTION TOO LONG: {func.name} in {rel_path}:{func.line_start} "
                    f"has {func.lines} lines (hard limit: {LIMITS['lines_per_function_hard']})"
                )
            elif func.lines > LIMITS["lines_per_function_soft"]:
                metrics.warnings.append(
                    f"FUNCTION TOO LONG: {func.name} in {rel_path}:{func.line_start} "
                    f"has {func.lines} lines (soft limit: {LIMITS['lines_per_function_soft']})"
                )

            # Complexity checks
            if func.complexity > LIMITS["cyclomatic_complexity_hard"]:
                metrics.violations.append(
                    f"HIGH COMPLEXITY: {func.name} in {rel_path}:{func.line_start} "
                    f"has complexity {func.complexity} (hard limit: {LIMITS['cyclomatic_complexity_hard']})"
                )
            elif func.complexity > LIMITS["cyclomatic_complexity_soft"]:
                metrics.warnings.append(
                    f"HIGH COMPLEXITY: {func.name} in {rel_path}:{func.line_start} "
                    f"has complexity {func.complexity} (soft limit: {LIMITS['cyclomatic_complexity_soft']})"
                )


def analyze_project(root_dir: Path) -> ProjectMetrics:
    """Analyze entire project."""
    metrics = ProjectMetrics()
    for file_path in find_python_files(root_dir):
        file_metrics = analyze_file(file_path)
        metrics.files.append(file_metrics)
        metrics.total_lines += file_metrics.total_lines
        metrics.total_code_lines += file_metrics.code_lines
        metrics.total_functions += len(file_metrics.functions)

    metrics.total_files = len(metrics.files)
    _check_violations(metrics, root_dir)
    return metrics


def find_dead_code(root_dir: Path) -> list[str]:
    """Find potentially dead code."""
    dead_code = []
    for path in root_dir.rglob("*.py"):
        parts = path.relative_to(root_dir).parts
        if any(part in EXCLUDE_DIRS for part in parts):
            continue
        # Skip tests/ directory - that's where tests belong
        if "tests" in parts:
            continue
        name = path.name
        if name.startswith("test_") and "src" not in str(path) and "tests_archive" not in str(path):
            dead_code.append(f"Possible orphan test file: {path.relative_to(root_dir)}")
        if name.startswith(("debug_", "analyze_", "check_")):
            dead_code.append(f"Possible debug script (review): {path.relative_to(root_dir)}")
    return dead_code


def _parse_coverage_output(output: str) -> tuple[int, int]:
    """Parse coverage output to get statement counts for core modules."""
    # Core modules that must maintain coverage
    core_modules = [
        "config",
        "config_validator",
        "influx_client",
        "json_logger",
        "logger",
        "heating_curve",
        "heating_data_fetcher",
        "heating_optimizer",
        "program_executor",
        "program_generator",
        "pump_controller",
        "checkwatt",
        "energy_meter",
        "shelly_em3",
        "spot_prices",
        "temperature",
        "weather",
        "windpower",
        "analytics_15min",
        "analytics_1hour",
        "emeters_5min",
    ]
    total_stmts, total_miss = 0, 0

    for line in output.split("\n"):
        for mod in core_modules:
            if mod + ".py" in line and "%" in line and "test_" not in line:
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        total_stmts += int(parts[1])
                        total_miss += int(parts[2])
                    except (ValueError, IndexError):
                        pass
    return total_stmts, total_miss


def _parse_per_file_coverage(output: str, min_coverage: int = 90) -> tuple[list[str], list[str]]:
    """Parse coverage output to find files below minimum coverage threshold.

    Returns:
        Tuple of (files_below_threshold, all_coverage_lines)
        files_below_threshold: List of "filename: X%" strings for files below threshold
    """
    files_below = []
    all_lines = []

    # Files to exclude from per-file coverage check (test files, __init__, etc.)
    exclude_patterns = ["test_", "__init__", "conftest"]

    for line in output.split("\n"):
        # Coverage lines look like: "filename.py    100    10    90%"
        if ".py" in line and "%" in line:
            # Skip test files and other exclusions
            if any(pat in line for pat in exclude_patterns):
                continue

            parts = line.split()
            if len(parts) >= 4:
                filename = parts[0]
                try:
                    # Extract percentage (remove % sign)
                    pct_str = parts[3].replace("%", "")
                    coverage_pct = int(pct_str)
                    all_lines.append(f"{filename}: {coverage_pct}%")

                    if coverage_pct < min_coverage:
                        files_below.append(f"{filename}: {coverage_pct}%")
                except (ValueError, IndexError):
                    pass

    return files_below, all_lines


def run_coverage_check(root_dir: Path) -> tuple[bool, str]:
    """Run pytest with coverage and check against limits.

    Checks both:
    1. Aggregate coverage for core modules (must meet test_coverage_min)
    2. Per-file coverage (all files must be >= 90%)
    """
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

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=root_dir, timeout=120)
        output = result.stdout + result.stderr
        total_stmts, total_miss = _parse_coverage_output(output)

        # Check aggregate coverage for core modules
        core_passed = True
        if total_stmts > 0:
            coverage_pct = 100 * (total_stmts - total_miss) / total_stmts
            core_passed = coverage_pct >= LIMITS["test_coverage_min"]
            output += f"\n\nCore modules coverage: {coverage_pct:.1f}% ({total_stmts - total_miss}/{total_stmts} statements)"
        else:
            core_passed = False
            output += "\n\nCould not calculate core module coverage"

        # Check per-file coverage (all files must be >= 90%)
        files_below, _ = _parse_per_file_coverage(output, min_coverage=90)
        per_file_passed = len(files_below) == 0

        if files_below:
            output += f"\n\n{FAIL} Files below 90% coverage:\n"
            for f in files_below:
                output += f"  - {f}\n"

        passed = core_passed and per_file_passed
        return passed, output
    except subprocess.TimeoutExpired:
        return False, "Coverage check timed out"
    except Exception as e:
        return False, f"Coverage check failed: {e}"


def _run_dead_code_scan(root_dir: Path):
    """Run and print dead code scan."""
    print(f"\n{'='*70}")
    print("DEAD CODE SCAN")
    print("=" * 70)
    dead = find_dead_code(root_dir)
    if dead:
        for item in dead:
            print(f"  [??] {item}")
    else:
        print("  No obvious dead code found")


def run_ruff_check(root_dir: Path) -> tuple[bool, str, int]:
    """Run ruff linter. Returns (passed, output, error_count)."""
    cmd = [sys.executable, "-m", "ruff", "check", "."]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=root_dir, timeout=60)
        output = result.stdout + result.stderr
        # Count errors (each line starting with path is an error)
        error_count = len(
            [
                line
                for line in output.strip().split("\n")
                if line and ":" in line and not line.startswith("Found")
            ]
        )
        passed = result.returncode == 0
        return passed, output, error_count
    except subprocess.TimeoutExpired:
        return False, "Ruff check timed out", 0
    except Exception as e:
        return False, f"Ruff check failed: {e}", 0


def run_mypy_check(root_dir: Path) -> tuple[bool, str, int]:
    """Run mypy type checker. Returns (passed, output, error_count)."""
    # Check src/ directory
    targets = ["src/"]
    existing_targets = [t for t in targets if (root_dir / t).exists()]

    cmd = [sys.executable, "-m", "mypy"] + existing_targets
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=root_dir, timeout=120)
        output = result.stdout + result.stderr
        # Count errors
        error_count = len([line for line in output.strip().split("\n") if ": error:" in line])
        passed = result.returncode == 0
        return passed, output, error_count
    except subprocess.TimeoutExpired:
        return False, "Mypy check timed out", 0
    except Exception as e:
        return False, f"Mypy check failed: {e}", 0


def _print_lint_result(
    name: str, passed: bool, output: str, error_count: int, filter_str: str = ""
):
    """Print lint result for a single tool."""
    if passed:
        print(f"  {OK} {name}: No issues found")
        return
    print(f"  {FAIL} {name}: {error_count} issues found")
    if filter_str:
        lines = [ln for ln in output.strip().split("\n") if filter_str in ln][:20]
    else:
        lines = [ln for ln in output.strip().split("\n") if ln][:20]
    for line in lines:
        print(f"      {line}")
    if error_count > 20:
        print(f"      ... and {error_count - 20} more")


def _run_lint_checks(root_dir: Path) -> tuple[bool, bool]:
    """Run ruff and mypy. Returns (ruff_passed, mypy_passed)."""
    print(f"\n{'='*70}")
    print("LINT CHECKS")
    print("=" * 70)

    print("\n[Ruff] Running linter...")
    ruff_passed, ruff_output, ruff_errors = run_ruff_check(root_dir)
    _print_lint_result("Ruff", ruff_passed, ruff_output, ruff_errors)

    print("\n[Mypy] Running type checker...")
    mypy_passed, mypy_output, mypy_errors = run_mypy_check(root_dir)
    _print_lint_result("Mypy", mypy_passed, mypy_output, mypy_errors, ": error:")

    return ruff_passed, mypy_passed


def _run_coverage_report(root_dir: Path) -> bool:
    """Run and print coverage report. Returns True if failed."""
    print(f"\n{'='*70}")
    print("COVERAGE CHECK")
    print("=" * 70)
    print(f"Minimum required: {LIMITS['test_coverage_min']}%")
    passed, output = run_coverage_check(root_dir)
    lines = output.strip().split("\n")
    for line in lines:
        print(f"  {line}")
    if passed:
        print(f"\n{OK} Coverage check passed")
    else:
        print(f"\n{FAIL} Coverage below {LIMITS['test_coverage_min']}%")
    return not passed


def _parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Code quality metrics")
    parser.add_argument(
        "--check", action="store_true", help="Exit with error code if violations found"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed breakdown")
    parser.add_argument("--dead-code", action="store_true", help="Scan for potentially dead code")
    parser.add_argument(
        "--coverage", action="store_true", help="Run coverage check (requires pytest-cov)"
    )
    parser.add_argument("--lint", action="store_true", help="Run ruff and mypy checks")
    parser.add_argument(
        "--all", action="store_true", help="Run all checks (coverage + lint + dead-code)"
    )
    args = parser.parse_args()
    if args.all:
        args.coverage = True
        args.lint = True
        args.dead_code = True
    return args


def _check_exit_status(args, metrics: ProjectMetrics, lint_failed: bool, coverage_failed: bool):
    """Check for failures and exit if --check flag is set."""
    if not args.check:
        return
    if not (metrics.violations or coverage_failed or lint_failed):
        return
    issues = []
    if metrics.violations:
        issues.append(f"{len(metrics.violations)} code violations")
    if lint_failed:
        issues.append("lint errors")
    if coverage_failed:
        issues.append("coverage below threshold")
    print(f"\n{FAIL} Issues found: {', '.join(issues)}. Exiting with error.")
    sys.exit(1)


def main():
    args = _parse_args()
    root_dir = Path(__file__).parent

    metrics = analyze_project(root_dir)
    print_report(metrics, verbose=args.verbose)

    if args.dead_code:
        _run_dead_code_scan(root_dir)

    lint_failed = False
    if args.lint:
        ruff_passed, mypy_passed = _run_lint_checks(root_dir)
        lint_failed = not (ruff_passed and mypy_passed)

    coverage_failed = args.coverage and _run_coverage_report(root_dir)
    _check_exit_status(args, metrics, lint_failed, coverage_failed)

    return metrics


if __name__ == "__main__":
    main()
