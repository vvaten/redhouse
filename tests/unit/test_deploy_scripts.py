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


class TestHealthCheckUnit:
    """Warnings must not put the unit into failed state."""

    UNIT = REPO / "deployment" / "systemd" / "redhouse-health-check.service"

    def test_warning_exit_code_counts_as_success(self):
        """run_health_check returns 2 for warnings, which are mailed already.

        Without this the unit flaps failed on every warning and
        systemctl --failed stops meaning anything for it.
        """
        text = self.UNIT.read_text(encoding="utf-8")
        assert "SuccessExitStatus=2" in text

    def test_failures_still_fail_the_unit(self):
        """Exit 1 is a real failure and must not be whitelisted too."""
        text = self.UNIT.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("SuccessExitStatus="):
                assert line.split("=", 1)[1].split() == ["2"]


class TestStagingScheduleDoesNotCollide:
    """Staging must not fire its health check with production's."""

    GENERATOR = REPO / "deployment" / "generate_staging_systemd.sh"
    PROD_TIMER = REPO / "deployment" / "systemd" / "redhouse-health-check.timer"

    def test_production_still_on_the_quarter_hour(self):
        """The generator rewrites this exact string, so pin it."""
        assert "OnCalendar=*:00/15" in self.PROD_TIMER.read_text(encoding="utf-8")

    def test_generator_offsets_the_staging_health_check(self):
        text = self.GENERATOR.read_text(encoding="utf-8")
        assert "OnCalendar=*:10/15" in text

    def test_offset_avoids_the_deploy_windows(self):
        """A staging run inside a deploy window would race the deploy."""
        deploy = PRODUCTION_DEPLOY.read_text(encoding="utf-8")
        match = re.search(r"OPTIMAL_WINDOWS=\(([0-9 ]+)\)", deploy)
        assert match, "OPTIMAL_WINDOWS not found"
        windows = [int(x) for x in match.group(1).split()]
        staging_minutes = [10, 25, 40, 55]
        for start in windows:
            for minute in staging_minutes:
                assert not start <= minute <= start + 2, f"{minute} is inside window {start}"


class TestStagingTimersScript:
    SCRIPT = REPO / "deployment" / "staging_timers.sh"

    @pytest.mark.parametrize("timer", ["temperature", "health-check"])
    def test_is_not_auto_started(self, timer):
        """Both do nothing useful in staging, one of them by email."""
        text = self.SCRIPT.read_text(encoding="utf-8")
        match = re.search(r"NO_AUTO_START=\((.*?)^\)", text, re.S | re.M)
        assert match, "NO_AUTO_START not found"
        assert f'"{timer}"' in match.group(1)

    def test_temperature_is_still_startable_by_name(self):
        """Skipping it from start-all must not make the name invalid."""
        text = self.SCRIPT.read_text(encoding="utf-8")
        match = re.search(r"^TIMERS=\((.*?)^\)", text, re.S | re.M)
        assert match, "TIMERS not found"
        assert '"temperature"' in match.group(1)
