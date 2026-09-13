"""Tests for the shell logic in the deploy scripts."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
PRODUCTION_DEPLOY = REPO / "deployment" / "deploy_production.sh"
STAGING_DEPLOY = REPO / "deployment" / "deploy_staging.sh"
WAS_RUNNING_TEST = REPO / "tests" / "shell" / "was_running.sh"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
class TestWasRunningMatcher:
    def test_shell_assertions_pass(self):
        """Guards the whitespace bug that disabled every timer.

        RUNNING_TIMERS comes from a pipeline, so it is newline separated,
        while was_running compares on surrounding spaces. Without the tr
        normalisation nothing matches and the deploy disables everything.
        """
        result = subprocess.run(
            ["bash", str(WAS_RUNNING_TEST)], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "FAIL" not in result.stdout


class TestCaptureNormalisesWhitespace:
    """The capture must produce a space separated list, not newlines."""

    def test_production_capture_pipes_through_tr(self):
        text = PRODUCTION_DEPLOY.read_text(encoding="utf-8")
        match = re.search(r"RUNNING_TIMERS=\$\((.*?)\)\n", text, re.S)
        assert match, "RUNNING_TIMERS assignment not found"
        assert "tr '\\n' ' '" in match.group(1), "capture must normalise newlines"

    def test_production_matcher_is_space_based(self):
        text = PRODUCTION_DEPLOY.read_text(encoding="utf-8")
        assert '*" $1 "*)' in text, "was_running should match on surrounding spaces"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
class TestScriptsParse:
    @pytest.mark.parametrize("script", [PRODUCTION_DEPLOY, STAGING_DEPLOY])
    def test_syntax_is_valid(self, script):
        result = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stderr


class TestBothScriptsRestoreOnExit:
    """A failed deploy must not leave an environment stopped."""

    @pytest.mark.parametrize("script", [PRODUCTION_DEPLOY, STAGING_DEPLOY])
    def test_trap_on_exit_present(self, script):
        text = script.read_text(encoding="utf-8")
        assert "trap restore_timers EXIT" in text
