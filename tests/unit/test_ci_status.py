"""The deploy's CI gate must pass only a fully green commit."""

import importlib.util
import urllib.error
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "deployment" / "ci_status.py"


def _load():
    spec = importlib.util.spec_from_file_location("ci_status_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(name, status="completed", conclusion="success"):
    return {"name": name, "status": status, "conclusion": conclusion}


def _payload(*runs, total=None):
    return {"total_count": len(runs) if total is None else total, "check_runs": list(runs)}


class TestVerdict:
    def test_all_green_passes(self):
        green, reason = _load().verdict(_payload(_run("lint"), _run("test (3.9)")))
        assert green and "2 check runs" in reason

    def test_one_failure_blocks(self):
        green, reason = _load().verdict(_payload(_run("lint", conclusion="failure"), _run("t")))
        assert not green and "lint: completed/failure" in reason

    def test_a_running_job_blocks(self):
        assert _load().verdict(_payload(_run("lint"), _run("t", "in_progress", None)))[0] is False

    def test_no_runs_blocks(self):
        """A push that CI has not picked up yet is not green."""
        assert _load().verdict(_payload()) == (False, "no check runs yet")

    def test_skipped_and_neutral_block(self):
        for conclusion in ("skipped", "neutral", "cancelled", "timed_out"):
            assert not _load().verdict(_payload(_run("lint", conclusion=conclusion)))[0]

    def test_runs_beyond_the_page_block(self):
        """A failure on a page not read must not pass as green."""
        green, reason = _load().verdict(_payload(_run("lint"), total=101))
        assert not green and "1 of 101" in reason


class TestMain:
    def test_green_exits_zero(self, capsys):
        module = _load()
        with patch.object(module, "fetch", return_value=_payload(_run("lint"))):
            assert module.main(["ci_status.py", "abc123"]) == 0
        assert "abc123" in capsys.readouterr().out

    def test_network_failure_blocks(self, capsys):
        module = _load()
        with patch.object(module, "fetch", side_effect=urllib.error.URLError("down")):
            assert module.main(["ci_status.py", "abc123"]) == 1
        assert "could not read CI status" in capsys.readouterr().out

    def test_unexpected_payload_blocks(self):
        module = _load()
        with patch.object(module, "fetch", return_value={"message": "Not Found"}):
            assert module.main(["ci_status.py", "abc123"]) == 1

    def test_usage_error(self):
        assert _load().main(["ci_status.py"]) == 2

    def test_url_names_the_repo_and_commit_and_reads_a_full_page(self):
        url = _load().check_runs_url("abc")
        assert url.endswith("/repos/vvaten/redhouse/commits/abc/check-runs?per_page=100")
