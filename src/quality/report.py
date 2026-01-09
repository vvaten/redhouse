"""Report generation and printing for code quality metrics.

Provides formatted output for project metrics.
"""

from pathlib import Path

from src.quality.analyzers import ProjectMetrics

# Quality limits (shared with main module)
LIMITS = {
    "lines_per_file": 500,
    "lines_per_function": 50,
    "cyclomatic_complexity": 10,
    "test_coverage_min": 90,
}

OK = "[OK]"
FAIL = "[!!]"


def _print_summary(metrics: ProjectMetrics):
    """Print project summary section."""
    print("=" * 70)
    print("CODE QUALITY REPORT")
    print("=" * 70)
    print("\nProject Summary:")
    print(f"  Files analyzed: {metrics.total_files}")
    print(f"  Total lines: {metrics.total_lines}")
    print(f"  Code lines: {metrics.total_code_lines}")
    print(f"  Total functions: {metrics.total_functions}")
    print("\nLimits:")
    print(f"  Lines per file: {LIMITS['lines_per_file']}")
    print(f"  Lines per function: {LIMITS['lines_per_function']}")
    print(f"  Cyclomatic complexity: {LIMITS['cyclomatic_complexity']}")


def _print_top_files(metrics: ProjectMetrics):
    """Print top files by size."""
    print("\nFiles by size (top 10):")
    sorted_files = sorted(metrics.files, key=lambda f: f.total_lines, reverse=True)
    for fm in sorted_files[:10]:
        status = FAIL if fm.total_lines > LIMITS["lines_per_file"] else OK
        print(
            f"  {status} {Path(fm.path).name}: {fm.total_lines} lines, {len(fm.functions)} functions"
        )


def _print_top_functions(metrics: ProjectMetrics):
    """Print top functions by complexity and length."""
    all_funcs = [f for fm in metrics.files for f in fm.functions]

    print("\nFunctions by complexity (top 10):")
    for func in sorted(all_funcs, key=lambda f: f.complexity, reverse=True)[:10]:
        status = FAIL if func.complexity > LIMITS["cyclomatic_complexity"] else OK
        print(
            f"  {status} {func.name} ({Path(func.file).name}:{func.line_start}): "
            f"complexity={func.complexity}, lines={func.lines}"
        )

    print("\nFunctions by length (top 10):")
    for func in sorted(all_funcs, key=lambda f: f.lines, reverse=True)[:10]:
        status = FAIL if func.lines > LIMITS["lines_per_function"] else OK
        print(
            f"  {status} {func.name} ({Path(func.file).name}:{func.line_start}): "
            f"lines={func.lines}, complexity={func.complexity}"
        )


def _print_violations(metrics: ProjectMetrics):
    """Print violations section."""
    if metrics.violations:
        print(f"\n{'='*70}")
        print(f"VIOLATIONS ({len(metrics.violations)}):")
        print("=" * 70)
        for v in metrics.violations:
            print(f"  {FAIL} {v}")
    else:
        print(f"\n{OK} No violations found!")


def print_report(metrics: ProjectMetrics, verbose: bool = False):
    """Print metrics report."""
    _print_summary(metrics)
    _print_top_files(metrics)
    _print_top_functions(metrics)
    _print_violations(metrics)

    if verbose:
        _print_verbose_breakdown(metrics)


def _print_verbose_breakdown(metrics: ProjectMetrics):
    """Print detailed file breakdown."""
    print(f"\n{'='*70}")
    print("DETAILED FILE BREAKDOWN")
    print("=" * 70)
    sorted_files = sorted(metrics.files, key=lambda f: f.total_lines, reverse=True)
    for fm in sorted_files:
        try:
            rel_path = Path(fm.path).relative_to(Path(__file__).parent)
        except ValueError:
            rel_path = Path(fm.path)
        print(f"\n{rel_path}:")
        print(f"  Lines: {fm.total_lines} (code: {fm.code_lines})")
        print(f"  Classes: {', '.join(fm.classes) if fm.classes else 'none'}")
        print(f"  Functions: {len(fm.functions)}")
        for func in sorted(fm.functions, key=lambda f: f.line_start):
            print(
                f"    - {func.name}:{func.line_start} (lines={func.lines}, complexity={func.complexity})"
            )
