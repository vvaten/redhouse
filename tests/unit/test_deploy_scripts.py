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
ALWAYS_ON_TEST = REPO / "tests" / "shell" / "is_always_on.sh"

# Resolve the executable, never the bare name. Windows resolves "bash"
# to System32\bash.exe, which is WSL and cannot see C:\Projects, so
# every one of these tests exited 127 without asserting anything.
BASH = shutil.which("bash")


@pytest.mark.skipif(BASH is None, reason="bash not available")
class TestWasRunningMatcher:
    def test_shell_assertions_pass(self):
        """Guards the whitespace bug that disabled every timer.

        RUNNING_TIMERS comes from a pipeline, so it is newline separated,
        while was_running compares on surrounding spaces. Without the tr
        normalisation nothing matches and the deploy disables everything.
        """
        result = subprocess.run(
            [BASH, str(WAS_RUNNING_TEST)], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "FAIL" not in result.stdout

    def test_always_on_matcher_executes(self):
        """The matcher that keeps the watchdog enabled through a deploy."""
        result = subprocess.run(
            [BASH, str(ALWAYS_ON_TEST)], capture_output=True, text=True, timeout=60
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


@pytest.mark.skipif(BASH is None, reason="bash not available")
class TestScriptsParse:
    @pytest.mark.parametrize("script", [PRODUCTION_DEPLOY, STAGING_DEPLOY])
    def test_syntax_is_valid(self, script):
        result = subprocess.run(
            [BASH, "-n", str(script)], capture_output=True, text=True, timeout=60
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


def _health_check_minute(timer_text: str) -> int:
    match = re.search(r"^OnCalendar=\*:(\d{1,2})/15$", timer_text, re.M)
    assert match, "health check OnCalendar not found"
    return int(match.group(1))


def _deploy_windows() -> list:
    deploy = PRODUCTION_DEPLOY.read_text(encoding="utf-8")
    match = re.search(r"OPTIMAL_WINDOWS=\(([0-9 ]+)\)", deploy)
    assert match, "OPTIMAL_WINDOWS not found"
    return [int(x) for x in match.group(1).split()]


@pytest.mark.skipif(BASH is None, reason="bash not available")
class TestStagingScheduleDoesNotCollide:
    """Staging must not fire its health check with production's."""

    GENERATOR = REPO / "deployment" / "generate_staging_systemd.sh"
    PROD_TIMER = REPO / "deployment" / "systemd" / "redhouse-health-check.timer"

    def _staging_minute(self, tmp_path) -> int:
        """Run the generator's own sed, so the assertion is behavioural.

        Asserting the string appears in the script passed while the
        pattern matched nothing, which is how the offset could break.
        """
        text = self.GENERATOR.read_text(encoding="utf-8")
        expr = re.search(r"sed -i -E '([^']+)'", text)
        assert expr, "generator sed expression not found"
        target = tmp_path / "staging.timer"
        target.write_text(self.PROD_TIMER.read_text(encoding="utf-8"), encoding="utf-8")
        result = subprocess.run(
            [BASH, "-c", f"sed -i -E '{expr.group(1)}' '{target.as_posix()}'"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        return _health_check_minute(target.read_text(encoding="utf-8"))

    def test_generator_actually_rewrites_the_minute(self, tmp_path):
        """The sed must fire against whatever production currently uses."""
        prod = _health_check_minute(self.PROD_TIMER.read_text(encoding="utf-8"))
        assert self._staging_minute(tmp_path) != prod

    def test_both_minutes_avoid_the_deploy_windows(self, tmp_path):
        """A run inside a deploy window would race the deploy."""
        prod = _health_check_minute(self.PROD_TIMER.read_text(encoding="utf-8"))
        staging = self._staging_minute(tmp_path)
        for start in _deploy_windows():
            for label, minute in [("production", prod), ("staging", staging)]:
                for m in range(minute, 60, 15):
                    assert not start <= m <= start + 2, f"{label} :{m} in window {start}"

    def test_production_is_off_the_quarter_hour(self):
        """Every read timeout in 7 days landed on :00, :15, :30 or :45."""
        prod = _health_check_minute(self.PROD_TIMER.read_text(encoding="utf-8"))
        assert prod % 15 != 0, "health check is back on the quarter hour"


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


class TestHeatingControlUnits:
    """The two units that will eventually drive the heat pump."""

    EXECUTE = REPO / "deployment" / "systemd" / "redhouse-execute-program.service"
    GENERATE = REPO / "deployment" / "systemd" / "redhouse-generate-program.timer"

    def test_executor_is_dry_run_until_cutover(self):
        """Removing --dry-run hands pump control to redhouse.

        This test is meant to fail at the Phase 6 cutover, so that the
        removal is deliberate and lands in the same commit as its
        justification. Do not delete it to make a deploy pass.
        """
        text = self.EXECUTE.read_text(encoding="utf-8")
        exec_start = [ln for ln in text.splitlines() if ln.startswith("ExecStart=")]
        assert len(exec_start) == 1, exec_start
        assert "--dry-run" in exec_start[0]

    def test_generate_does_not_collide_with_wibatemp(self):
        """wibatemp generates the controlling program at 16:05."""
        text = self.GENERATE.read_text(encoding="utf-8")
        match = re.search(r"^OnCalendar=\S+ (\d{2}):(\d{2}):(\d{2})$", text, re.M)
        assert match, "OnCalendar not found"
        hour, minute = int(match.group(1)), int(match.group(2))
        assert (hour, minute) != (16, 5), "must not run with wibatemp's generator"

    def test_generate_avoids_the_deploy_windows(self):
        """A run inside a deploy window would race the deploy."""
        text = self.GENERATE.read_text(encoding="utf-8")
        match = re.search(r"^OnCalendar=\S+ \d{2}:(\d{2}):\d{2}$", text, re.M)
        assert match, "OnCalendar not found"
        minute = int(match.group(1))
        deploy = PRODUCTION_DEPLOY.read_text(encoding="utf-8")
        win = re.search(r"OPTIMAL_WINDOWS=\(([0-9 ]+)\)", deploy)
        assert win, "OPTIMAL_WINDOWS not found"
        for start in (int(x) for x in win.group(1).split()):
            assert not start <= minute <= start + 2, f"{minute} is inside window {start}"


class TestTimerSchedulesAreNotUnioned:
    """systemd unions OnCalendar lines, it does not replace them.

    Three collectors carried OnCalendar=hourly next to an offset entry,
    so each ran twice an hour and all three converged on :00.
    """

    SHORTHAND = {"minutely", "hourly", "daily", "weekly", "monthly", "yearly", "annually"}
    UNITS = sorted((REPO / "deployment" / "systemd").glob("*.timer"))

    def test_units_exist(self):
        assert self.UNITS, "no timer units found"

    def test_no_shorthand_beside_an_explicit_schedule(self):
        offenders = []
        for unit in self.UNITS:
            lines = [
                ln.split("=", 1)[1].strip()
                for ln in unit.read_text(encoding="utf-8").splitlines()
                if ln.startswith("OnCalendar=")
            ]
            if len(lines) > 1 and any(v.lower() in self.SHORTHAND for v in lines):
                offenders.append(f"{unit.name}: {lines}")
        assert not offenders, "shorthand unions with explicit times: " + "; ".join(offenders)


class TestMonitoringTimersSurviveADeploy:
    """The deploy disables anything that was not already running.

    redhouse-program-check was installed by the very deploy that then
    disabled it, and health_check skips disabled timers, so nothing
    reported the silence.
    """

    UNITS = REPO / "deployment" / "systemd"

    def test_production_deploy_has_an_always_on_list(self):
        text = PRODUCTION_DEPLOY.read_text(encoding="utf-8")
        match = re.search(r"ALWAYS_ON=\((.*?)^\)", text, re.S | re.M)
        assert match, "ALWAYS_ON not found"
        assert '"redhouse-program-check"' in match.group(1)

    def test_always_on_bypasses_the_was_running_gate(self):
        text = PRODUCTION_DEPLOY.read_text(encoding="utf-8")
        assert 'was_running "$timer.timer" || is_always_on "$timer"' in text

    def test_every_always_on_timer_exists(self):
        text = PRODUCTION_DEPLOY.read_text(encoding="utf-8")
        match = re.search(r"ALWAYS_ON=\((.*?)^\)", text, re.S | re.M)
        assert match
        for name in re.findall(r'"([^"]+)"', match.group(1)):
            assert (self.UNITS / f"{name}.timer").is_file(), name


class TestDailyTimersCatchUp:
    """A once-a-day timer without catch-up skips a day on reboot."""

    UNITS = sorted((REPO / "deployment" / "systemd").glob("*.timer"))

    def test_daily_timers_are_persistent(self):
        offenders = []
        for unit in self.UNITS:
            text = unit.read_text(encoding="utf-8")
            schedules = [
                ln.split("=", 1)[1].strip()
                for ln in text.splitlines()
                if ln.startswith("OnCalendar=")
            ]
            daily = [s for s in schedules if re.match(r"^\*-\*-\* \d{2}:\d{2}:\d{2}$", s)]
            if not daily:
                continue
            if "Persistent=true" not in text and "OnBootSec" not in text:
                offenders.append(unit.name)
        assert not offenders, f"daily timers with no catch-up: {offenders}"
