"""Code quality analysis package.

Provides AST-based metrics analysis and reporting.
"""

from src.quality.analyzers import (
    FileMetrics,
    FunctionMetrics,
    ProjectMetrics,
    analyze_file,
    calculate_complexity,
)
from src.quality.report import FAIL, LIMITS, OK, print_report

__all__ = [
    "ProjectMetrics",
    "FileMetrics",
    "FunctionMetrics",
    "analyze_file",
    "calculate_complexity",
    "LIMITS",
    "OK",
    "FAIL",
    "print_report",
]
