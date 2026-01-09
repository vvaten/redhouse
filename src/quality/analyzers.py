"""AST-based code analysis for quality metrics.

Provides cyclomatic complexity calculation and file/function analysis.
"""

import ast
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FunctionMetrics:
    """Metrics for a single function."""

    name: str
    file: str
    line_start: int
    line_end: int
    lines: int
    complexity: int


@dataclass
class FileMetrics:
    """Metrics for a single file."""

    path: str
    total_lines: int
    code_lines: int
    functions: list[FunctionMetrics] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    imports: int = 0


@dataclass
class ProjectMetrics:
    """Aggregate project metrics."""

    files: list[FileMetrics] = field(default_factory=list)
    total_files: int = 0
    total_lines: int = 0
    total_code_lines: int = 0
    total_functions: int = 0
    violations: list[str] = field(default_factory=list)


class CyclomaticComplexityVisitor(ast.NodeVisitor):
    """Calculate cyclomatic complexity of a function."""

    def __init__(self):
        self.complexity = 1

    def visit_If(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_For(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_While(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_With(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        self.complexity += len(node.values) - 1
        self.generic_visit(node)

    def visit_comprehension(self, node):
        self.complexity += 1 + len(node.ifs)
        self.generic_visit(node)

    def visit_IfExp(self, node):
        self.complexity += 1
        self.generic_visit(node)


def calculate_complexity(node: ast.AST) -> int:
    """Calculate cyclomatic complexity of an AST node."""
    visitor = CyclomaticComplexityVisitor()
    visitor.visit(node)
    return visitor.complexity


def analyze_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef, file_path: Path
) -> FunctionMetrics:
    """Analyze a single function."""
    line_start = node.lineno
    line_end = getattr(node, "end_lineno", line_start + 10)
    return FunctionMetrics(
        name=node.name,
        file=str(file_path),
        line_start=line_start,
        line_end=line_end,
        lines=line_end - line_start + 1,
        complexity=calculate_complexity(node),
    )


def _analyze_class_methods(class_node: ast.ClassDef, file_path: Path) -> list[FunctionMetrics]:
    """Extract method metrics from a class."""
    methods = []
    for item in class_node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_metrics = analyze_function(item, file_path)
            func_metrics.name = f"{class_node.name}.{func_metrics.name}"
            methods.append(func_metrics)
    return methods


def _parse_ast_for_metrics(tree: ast.AST, file_path: Path, metrics: FileMetrics):
    """Extract imports, classes, and functions from AST."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            metrics.imports += 1

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            metrics.classes.append(node.name)
            metrics.functions.extend(_analyze_class_methods(node, file_path))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            metrics.functions.append(analyze_function(node, file_path))


def analyze_file(file_path: Path) -> FileMetrics:
    """Analyze a single Python file."""
    try:
        source = file_path.read_text(encoding="utf-8")
        lines = source.split("\n")
    except Exception:
        return FileMetrics(path=str(file_path), total_lines=0, code_lines=0)

    code_lines = sum(1 for line in lines if line.strip() and not line.strip().startswith("#"))
    metrics = FileMetrics(path=str(file_path), total_lines=len(lines), code_lines=code_lines)

    try:
        tree = ast.parse(source)
        _parse_ast_for_metrics(tree, file_path, metrics)
    except SyntaxError:
        pass

    return metrics
