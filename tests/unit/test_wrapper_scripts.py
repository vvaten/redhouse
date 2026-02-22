"""Smoke tests for top-level wrapper scripts.

Verifies that each systemd entry-point wrapper can be imported without
error and exposes a callable 'main'.  A wrong import path (e.g. pointing
at a module that has no 'main') raises ImportError at import time, which
these tests will catch immediately.
"""

import importlib.util
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent


def _load_wrapper(filename: str):
    """Load a top-level wrapper script as a module and return it."""
    path = REPO_ROOT / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None, f"Cannot create spec for {filename}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "wrapper",
    [
        "aggregate_emeters_5min.py",
        "aggregate_analytics_15min.py",
        "aggregate_analytics_1hour.py",
    ],
)
def test_wrapper_imports_main(wrapper: str) -> None:
    """Each wrapper script must import and expose a callable 'main'."""
    mod = _load_wrapper(wrapper)
    assert callable(mod.main), f"{wrapper}: 'main' is not callable"
