"""Tests for the shell logic in the deploy scripts."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src.control import pump_restore
from src.control.pump_controller import PumpController

REPO = Path(__file__).parents[2]
# The request timeout of the timedatectl call
TIMEDATECTL_TIMEOUT = 10
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


def _timezone_gate(script: Path) -> tuple[str, int]:
    text = script.read_text(encoding="utf-8")
    match = re.search(r"HOST_TIMEZONE=.*?\nfi\n", text, re.S)
    assert match, f"no timezone gate in {script.name}"
    return match.group(0), match.start()


class TestTimezoneGate:
    """The local-time timers are only right on a host on Helsinki time."""

    @pytest.mark.parametrize("script", [PRODUCTION_DEPLOY, STAGING_DEPLOY])
    def test_gate_runs_before_the_timers_stop(self, script):
        _, start = _timezone_gate(script)
        assert start < script.read_text(encoding="utf-8").index("RUNNING_TIMERS=")

    @pytest.mark.skipif(BASH is None, reason="bash not available")
    @pytest.mark.parametrize("script", [PRODUCTION_DEPLOY, STAGING_DEPLOY])
    @pytest.mark.parametrize("zone, code", [("Europe/Helsinki", 0), ("UTC", 1), ("", 1)])
    def test_gate_refuses_any_other_zone(self, script, zone, code):
        gate, _ = _timezone_gate(script)
        stub = f"timedatectl() {{ echo '{zone}'; }}\n"
        result = subprocess.run([BASH, "-c", stub + gate], capture_output=True, timeout=60)
        assert result.returncode == code


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
    match = re.search(r"^OnCalendar=\*:(\d{1,2})/15( UTC)?$", timer_text, re.M)
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

    @pytest.mark.parametrize(
        "timer", ["temperature", "health-check", "program-check", "program-fallback"]
    )
    def test_is_not_auto_started(self, timer):
        """These do nothing useful in staging, three of them by email."""
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
    """The units that drive the heat pump and generate its program."""

    EXECUTE = REPO / "deployment" / "systemd" / "redhouse-execute-program.service"
    GENERATE = REPO / "deployment" / "systemd" / "redhouse-generate-program.timer"

    def test_pump_restore_drives_the_pump(self):
        """Since the cutover, redhouse, not wibatemp's cron line 27, restores at boot."""
        unit = REPO / "deployment" / "systemd" / "redhouse-pump-restore.service"
        exec_start = _values(unit.read_text(encoding="utf-8"), "ExecStart")
        assert len(exec_start) == 1 and "--dry-run" not in exec_start[0]

    def test_executor_and_restore_hand_over_together(self):
        """Both units drive the pump: one live and one dry-run is never right."""
        restore = REPO / "deployment" / "systemd" / "redhouse-pump-restore.service"
        dry = [
            "--dry-run" in _values(unit.read_text(encoding="utf-8"), "ExecStart")[0]
            for unit in (self.EXECUTE, restore)
        ]
        assert dry[0] == dry[1]

    def test_restore_runs_before_the_executor_with_room_to_finish(self):
        restore = REPO / "deployment" / "systemd" / "redhouse-pump-restore.service"
        text = restore.read_text(encoding="utf-8")
        assert "redhouse-execute-program.service" in " ".join(_values(text, "Before"))
        pc = PumpController
        relays = 2 * (pc.RELAY_ATTEMPTS - 1) * pc.RELAY_RETRY_WAIT
        waits = pump_restore.CLOCK_SYNC_TIMEOUT + pump_restore.LATE_SYNC_TIMEOUT
        overshoot = 2 * (pump_restore.CLOCK_SYNC_POLL + TIMEDATECTL_TIMEOUT)
        budget = waits + overshoot + 2 * (pc.EVU_CYCLE_DURATION + relays)
        assert int(_values(text, "TimeoutStartSec")[0]) >= budget

    def test_boot_units_follow_the_executor_and_are_never_started(self):
        """A rollback that stopped the executor must not get a live boot restore back."""
        text = PRODUCTION_DEPLOY.read_text(encoding="utf-8")
        units = _list(text, "BOOT_UNITS")
        assert units == ["redhouse-pump-restore"]
        for unit in units:
            assert (REPO / "deployment" / "systemd" / f"{unit}.service").is_file()
            assert not (REPO / "deployment" / "systemd" / f"{unit}.timer").exists()
        assert text.count('if should_run "redhouse-execute-program"; then') >= 2
        assert 'systemctl "$action" "$unit.service"' in text
        assert 'systemctl restart "$unit' not in text and 'systemctl start "$unit' not in text
        assert "verify_boot_units || STATES_OK=false" in text

    def test_executor_drives_the_pump(self):
        """Adding --dry-run back hands pump control away from redhouse, so it
        must be deliberate: change this test in the same commit."""
        text = self.EXECUTE.read_text(encoding="utf-8")
        exec_start = [ln for ln in text.splitlines() if ln.startswith("ExecStart=")]
        assert len(exec_start) == 1, exec_start
        assert "--dry-run" not in exec_start[0]

    FALLBACK = REPO / "deployment" / "systemd" / "redhouse-program-fallback.timer"
    SCHEDULE = re.compile(r"^\*-\*-\* (\d{2})\.\.(\d{2}):(\d{2}):00$")

    def _generate_schedule(self) -> tuple:
        """(first hour, last hour, minute) of the hourly retries."""
        schedules = _values(self.GENERATE.read_text(encoding="utf-8"), "OnCalendar")
        assert len(schedules) == 1, schedules
        match = self.SCHEDULE.match(schedules[0])
        assert match, f"not an hourly retry range: {schedules[0]}"
        return int(match.group(1)), int(match.group(2)), int(match.group(3))

    def test_generate_starts_at_16_06(self):
        first, _, minute = self._generate_schedule()
        assert (first, minute) == (16, 6)

    def test_generate_retries_until_late_evening_without_catchup(self):
        """A boot-time catch-up before the prices publish only fails."""
        first, last, _ = self._generate_schedule()
        assert (first, last) == (16, 23)
        assert not _has_catchup(self.GENERATE.read_text(encoding="utf-8"))

    def test_generate_skips_a_program_it_already_made(self):
        service = self.GENERATE.with_suffix(".service").read_text(encoding="utf-8")
        assert "--if-missing" in _values(service, "ExecStart")[0]

    def test_fallback_runs_after_the_last_retry(self):
        _, last, minute = self._generate_schedule()
        text = self.FALLBACK.read_text(encoding="utf-8")
        assert _values(text, "OnCalendar") == ["*-*-* 23:50:00"]
        assert (23, 50) > (last, minute)
        assert _has_catchup(text)

    def test_generate_avoids_the_deploy_windows(self):
        """A run inside a deploy window would race the deploy."""
        _, _, minute = self._generate_schedule()
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
        assert 'was_running "$1.timer" || is_always_on "$1"' in text
        assert text.count('if should_run "$timer"; then') == 2, "install and verify loops"

    def test_every_always_on_timer_exists(self):
        text = PRODUCTION_DEPLOY.read_text(encoding="utf-8")
        match = re.search(r"ALWAYS_ON=\((.*?)^\)", text, re.S | re.M)
        assert match
        for name in re.findall(r'"([^"]+)"', match.group(1)):
            assert (self.UNITS / f"{name}.timer").is_file(), name


TRIGGER_KEYS = (
    "OnCalendar",
    "OnBootSec",
    "OnStartupSec",
    "OnActiveSec",
    "OnUnitActiveSec",
    "OnUnitInactiveSec",
)


def _effective(text: str) -> dict:
    """Values of each live directive in order, as systemd reads them.
    Comments start with # or ;. An empty value resets that list, and an
    empty trigger resets every trigger."""
    values: dict = {}
    for ln in text.splitlines():
        if "=" in ln and not ln.lstrip().startswith(("#", ";")):
            key, value = (part.strip() for part in ln.split("=", 1))
            if not value and key in TRIGGER_KEYS:
                for trigger in TRIGGER_KEYS:
                    values[trigger] = []
            elif not value:
                values[key] = []
            elif key in values:
                values[key].append(value)
            else:
                values[key] = [value]
    return values


def _values(text: str, key: str) -> list:
    effective = _effective(text)
    return effective[key] if key in effective else []


def _directives(text: str) -> set:
    """Keys that still carry a value."""
    return {k for k, v in _effective(text).items() if v}


DAILY = re.compile(r"^(daily|(\*-\*-\* )?\d{1,2}:\d{2}(:\d{2})?)$")


def _daily_schedules(text: str) -> list:
    return [s for s in _values(text, "OnCalendar") if DAILY.match(s)]


def _has_catchup(text: str) -> bool:
    """A single-valued setting: the last assignment wins."""
    persistent = _values(text, "Persistent")
    return bool(persistent) and persistent[-1].lower() in {"1", "yes", "y", "true", "t", "on"}


class TestDailyTimersCatchUp:
    """A once-a-day timer without catch-up skips a day on reboot.

    One has none on purpose: a boot-time run before prices publish fails.
    """

    UNITS = sorted((REPO / "deployment" / "systemd").glob("*.timer"))
    # spot-prices already retries six times across the publishing window.
    NO_CATCHUP_BY_DESIGN = {
        "redhouse-spot-prices.timer",
    }

    def test_daily_timers_are_persistent(self):
        offenders = [
            u.name
            for u in self.UNITS
            if u.name not in self.NO_CATCHUP_BY_DESIGN
            and _daily_schedules(u.read_text(encoding="utf-8"))
            and not _has_catchup(u.read_text(encoding="utf-8"))
        ]
        assert not offenders, f"daily timers with no catch-up: {offenders}"

    def test_the_exemptions_are_still_daily_and_still_bare(self):
        """Stops the exemption list rotting into a silent excuse."""
        for name in self.NO_CATCHUP_BY_DESIGN:
            text = (REPO / "deployment" / "systemd" / name).read_text(encoding="utf-8")
            assert _daily_schedules(text), f"{name} is no longer a daily timer"
            assert not _has_catchup(text), f"{name} gained a catch-up; revisit the exemption"


class TestNoTriggerFiresOnRestart:
    """A deploy restarts timers, and these then fire off schedule:
    OnBootSec and OnStartupSec at once, OnActiveSec at its offset."""

    UNITS = sorted((REPO / "deployment" / "systemd").glob("*.timer"))
    ALLOWED = {"redhouse-temperature.timer"}
    FIRES_ON_RESTART = {"OnBootSec", "OnStartupSec", "OnActiveSec"}

    def test_units_exist(self):
        assert self.UNITS

    def test_only_temperature_has_one(self):
        offenders = [
            u.name
            for u in self.UNITS
            if u.name not in self.ALLOWED
            and self.FIRES_ON_RESTART & _directives(u.read_text(encoding="utf-8"))
        ]
        assert not offenders, f"restart-firing trigger reintroduced: {offenders}"

    def test_temperature_still_has_it(self):
        """OnBootSec is its only trigger that fires without an earlier run."""
        text = (REPO / "deployment" / "systemd" / "redhouse-temperature.timer").read_text(
            encoding="utf-8"
        )
        keys = _directives(text)
        assert "OnBootSec" in keys
        assert "OnUnitActiveSec" in keys
        assert "OnCalendar" not in keys


class TestEveryTimerFires:
    """A timer with no trigger that can fire first never runs its job."""

    UNITS = sorted((REPO / "deployment" / "systemd").glob("*.timer"))
    # OnUnitActiveSec and OnUnitInactiveSec need an earlier run first.
    FIRST_TRIGGERS = {"OnCalendar", "OnBootSec", "OnStartupSec", "OnActiveSec"}

    def test_units_exist(self):
        assert self.UNITS

    def test_every_timer_has_a_first_trigger(self):
        offenders = [
            u.name
            for u in self.UNITS
            if not self.FIRST_TRIGGERS & _directives(u.read_text(encoding="utf-8"))
        ]
        assert not offenders, f"timers that never fire: {offenders}"

    def test_a_commented_or_empty_trigger_does_not_count(self):
        text = "#OnBootSec=1min\n; OnCalendar=daily\nOnCalendar=\nOnUnitActiveSec=1min\n"
        assert not self.FIRST_TRIGGERS & _directives(text)

    def test_spaced_directives_are_parsed(self):
        assert "OnBootSec" in _directives("  OnBootSec = 5min\n")
        assert _has_catchup("Persistent = yes\n")
        assert _daily_schedules(" OnCalendar = *-*-* 16:06:00\n") == ["*-*-* 16:06:00"]

    def test_order_is_kept(self):
        assert "OnCalendar" not in _directives("OnCalendar=daily\nOnCalendar=\n")
        assert not set(TRIGGER_KEYS) & _directives("OnCalendar=daily\nOnBootSec=\n")
        assert "OnBootSec" in _directives("OnCalendar=\nOnBootSec=1min\n")
        assert not _has_catchup("Persistent=true\nPersistent=false\n")

    def test_short_daily_forms_are_daily(self):
        for form in ("daily", "16:06", "*-*-* 16:06", "*-*-* 16:06:00"):
            assert _daily_schedules(f"OnCalendar={form}\n") == [form], form
        assert _daily_schedules("OnCalendar=*:14/15\n") == []


def _list(text: str, name: str) -> list:
    match = re.search(rf"^{name}=\((.*?)^\)", text, re.S | re.M)
    assert match, f"{name} not found"
    return re.findall(r'"([^"]+)"', match.group(1))


class TestTimersStartNothing:
    """A dependency on the service starts it whenever the timer starts,
    and every deploy restarts the timers. A timer finds its same-named
    service without one."""

    UNITS = sorted((REPO / "deployment" / "systemd").glob("*.timer"))
    STARTS_UNITS = {"Requires", "Wants", "BindsTo", "Requisite", "PartOf", "Upholds"}

    def test_no_timer_depends_on_a_unit(self):
        offenders = {
            u.name: sorted(self.STARTS_UNITS & _directives(u.read_text(encoding="utf-8")))
            for u in self.UNITS
        }
        assert not {k: v for k, v in offenders.items() if v}

    def test_every_timer_has_a_same_named_service(self):
        for unit in self.UNITS:
            assert unit.with_suffix(".service").is_file(), unit.name
            assert "Unit" not in _directives(unit.read_text(encoding="utf-8")), unit.name

    def test_the_exit_trap_starts_only_stopped_timers(self):
        text = PRODUCTION_DEPLOY.read_text(encoding="utf-8")
        match = re.search(r"^restore_timers\(\) \{(.*?)^\}", text, re.S | re.M)
        assert match, "restore_timers not found"
        assert 'if ! systemctl is-active -q "$timer"; then' in match.group(1)

    def test_restart_exemptions_agree(self):
        """The deploy check exempts exactly the timers allowed to fire on restart."""
        allowed = {Path(n).stem for n in TestNoTriggerFiresOnRestart.ALLOWED}
        assert set(_list(PRODUCTION_DEPLOY.read_text(encoding="utf-8"), "FIRES_ON_RESTART")) == (
            allowed
        )


class TestEveryTimerIsDeployed:
    """A timer missing from a list is installed but never started."""

    NAMES = sorted(p.stem for p in (REPO / "deployment" / "systemd").glob("*.timer"))

    def test_production_deploy_lists_every_timer(self):
        assert sorted(_list(PRODUCTION_DEPLOY.read_text(encoding="utf-8"), "TIMERS")) == self.NAMES

    def test_install_lists_every_timer(self):
        text = (REPO / "deployment" / "deploy_install.sh").read_text(encoding="utf-8")
        assert sorted(_list(text, "TIMERS")) == self.NAMES

    def test_staging_lists_every_timer(self):
        text = (REPO / "deployment" / "staging_timers.sh").read_text(encoding="utf-8")
        staged = sorted(f"redhouse-{name}" for name in _list(text, "TIMERS"))
        assert staged == self.NAMES


class TestDeployGates:
    """The order of the gates matters: each must act before any change."""

    TEXT = PRODUCTION_DEPLOY.read_text(encoding="utf-8")

    def _at(self, needle: str) -> int:
        index = self.TEXT.find(needle)
        assert index >= 0, needle
        return index

    @pytest.mark.skipif(BASH is None, reason="bash not available")
    def test_gate_and_verification_execute(self):
        """Runs both functions from the script itself against stubs."""
        result = subprocess.run(
            [BASH, str(REPO / "tests" / "shell" / "deploy_gates.sh"), str(PRODUCTION_DEPLOY)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "FAIL" not in result.stdout

    def test_one_pinned_commit_is_gated_run_and_merged(self):
        pin = self._at(
            'DEPLOY_SHA="${REDHOUSE_DEPLOY_SHA:-$(sudo -u pi git rev-parse origin/main)}"'
        )
        gate = self._at('elif ! ci_gate "$DEPLOY_SHA"; then')
        reexec = self._at('git show "$DEPLOY_SHA:deployment/deploy_production.sh"')
        merge = self._at('git merge --ff-only "$DEPLOY_SHA"')
        assert pin < gate < reexec < merge
        assert 'REDHOUSE_DEPLOY_SHA="$DEPLOY_SHA"' in self.TEXT

    def test_unverified_code_never_runs_before_the_gate(self):
        gate = self._at('elif ! ci_gate "$DEPLOY_SHA"; then')
        assert gate < self._at('exec bash "$FRESH_SCRIPT" "$@"')
        assert gate < self._at("    wait_for_window\n")
        assert gate < self._at('systemctl stop "$timer"')

    def test_the_checker_is_not_the_commit_it_judges(self):
        """A commit that breaks ci_status.py must not pass its own gate."""
        assert 'git show "HEAD:deployment/ci_status.py"' in self.TEXT
        assert '"$1:deployment/ci_status.py"' not in self.TEXT

    def test_override_is_loud(self):
        assert "--skip-ci-check)" in self.TEXT
        assert "CI CHECK SKIPPED" in self.TEXT

    def test_reexec_cannot_loop(self):
        assert '[ -z "${REDHOUSE_DEPLOY_REEXEC:-}" ]; then' in self.TEXT
        assert 'REDHOUSE_DEPLOY_REEXEC="$FRESH_SCRIPT" REDHOUSE_DEPLOY_SHA=' in self.TEXT

    def test_verification_fails_the_deploy(self):
        verify = self._at("verify_timer_states || STATES_OK=false")
        assert verify > self._at('systemctl restart "$timer.timer"')
        assert (
            'echo "[FAIL] Deploy finished with timers or boot units in the wrong state"'
            in self.TEXT
        )
