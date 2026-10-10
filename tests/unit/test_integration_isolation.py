"""The integration conftest must keep every test away from real buckets."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from src.common.config import Config

REPO = Path(__file__).resolve().parents[2]
CONFTEST = REPO / "tests" / "integration" / "conftest.py"


def _load_conftest():
    spec = importlib.util.spec_from_file_location("integration_conftest_under_test", CONFTEST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bare_config() -> Config:
    """A Config that reads only the environment, without config.yaml."""
    return Config.__new__(Config)


def _clear_buckets(monkeypatch):
    for key in list(os.environ):
        if key.startswith("INFLUXDB_BUCKET_"):
            monkeypatch.delenv(key)


class TestBucketOverrides:
    def test_every_bucket_property_has_a_test_override(self, monkeypatch):
        conftest = _load_conftest()
        _clear_buckets(monkeypatch)
        for key, value in conftest.TEST_BUCKETS.items():
            monkeypatch.setenv(key, value)
        assert conftest.non_test_buckets(_bare_config()) == []

    def test_a_real_bucket_is_reported(self, monkeypatch):
        conftest = _load_conftest()
        _clear_buckets(monkeypatch)
        for key, value in conftest.TEST_BUCKETS.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("INFLUXDB_BUCKET_WINDPOWER", "windpower")
        offenders = conftest.non_test_buckets(_bare_config())
        assert offenders == ["influxdb_bucket_windpower=windpower"]

    def test_an_unset_bucket_is_reported(self, monkeypatch):
        conftest = _load_conftest()
        _clear_buckets(monkeypatch)
        offenders = conftest.non_test_buckets(_bare_config())
        assert len(offenders) == len(conftest.bucket_properties())
        assert all(o.endswith("(unset)") for o in offenders)

    def test_the_load_control_bucket_is_covered(self):
        """The bucket the heating program plan and its execution go to."""
        assert "influxdb_bucket_load_control" in _load_conftest().bucket_properties()


class TestOptIn:
    def test_skipped_without_the_variable(self, monkeypatch):
        monkeypatch.delenv("REDHOUSE_INTEGRATION", raising=False)
        assert _load_conftest().collect_ignore_glob == ["test_*.py"]

    def test_collected_with_the_variable(self, monkeypatch):
        monkeypatch.setenv("REDHOUSE_INTEGRATION", "1")
        assert not hasattr(_load_conftest(), "collect_ignore_glob")

    def test_a_file_named_on_the_command_line_is_skipped(self):
        """Explicit paths bypass collect_ignore_glob; the fixture skips them."""
        env = {k: v for k, v in os.environ.items() if k != "REDHOUSE_INTEGRATION"}
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs"]
            + ["tests/integration/test_safety_system.py"],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "1 skipped" in result.stdout
        assert "REDHOUSE_INTEGRATION=1" in result.stdout

    def test_no_integration_file_runs_as_a_script(self):
        """A direct run loads no conftest, so it would use the real buckets."""
        offenders = [
            p.name
            for p in (REPO / "tests" / "integration").glob("test_*.py")
            if "__main__" in p.read_text(encoding="utf-8")
        ]
        assert not offenders, offenders
