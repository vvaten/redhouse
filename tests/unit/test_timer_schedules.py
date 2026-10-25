"""Timers keep their rhythm across daylight saving time changes. On local
time, systemd fires a sub-daily timer only once in the repeated autumn hour,
and a daily timer at 03:xx is skipped on the spring change day."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

UNITS = sorted((Path(__file__).parents[2] / "deployment" / "systemd").glob("*.timer"))
DAILY = re.compile(r"^(\*-\*-\* )?(\d{1,2})(\.\.\d{1,2})?:\d{2}(:\d{2})?$")
SYSTEMD_ANALYZE = shutil.which("systemd-analyze")


def _schedules(path):
    return [
        line.split("=", 1)[1].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("OnCalendar=") and line.split("=", 1)[1].strip()
    ]


def test_the_units_are_found():
    assert len(UNITS) >= 10


@pytest.mark.parametrize("unit", UNITS, ids=lambda p: p.name)
def test_sub_daily_timers_run_on_utc_and_daily_ones_avoid_3am(unit):
    for schedule in _schedules(unit):
        daily = DAILY.match(schedule)
        if daily:
            assert daily.group(2) != "03", f"{unit.name}: {schedule} is lost on the spring change"
        else:
            assert schedule.endswith(" UTC"), f"{unit.name}: {schedule} skips the repeated hour"


def _elapses(spec, base):
    env = dict(os.environ, TZ="Europe/Helsinki")
    result = subprocess.run(
        [SYSTEMD_ANALYZE, "calendar", f"--base-time={base}", "--iterations=10", spec],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if result.returncode != 0 and "--base-time" in result.stderr:
        pytest.skip("this systemd-analyze has no --base-time")
    assert result.returncode == 0, result.stderr
    return re.findall(
        r"(?:Next elapse|Iter(?:ation|\.) #\d+): \w+ (\S+ \S+) (EET|EEST)", result.stdout
    )


@pytest.mark.skipif(SYSTEMD_ANALYZE is None, reason="systemd-analyze not available")
class TestAcrossTheChanges:
    def test_the_executor_runs_every_quarter_of_the_repeated_hour(self):
        elapses = _elapses("*:00,15,30,45:00 UTC", "2026-10-25 00:31:00 UTC")
        assert ("2026-10-25 03:00:00", "EET") in elapses
        assert ("2026-10-25 03:45:00", "EET") in elapses

    def test_the_executor_keeps_its_quarters_when_the_hour_is_skipped(self):
        elapses = _elapses("*:00,15,30,45:00 UTC", "2027-03-28 00:31:00 UTC")
        assert ("2027-03-28 02:45:00", "EET") in elapses
        assert ("2027-03-28 04:00:00", "EEST") in elapses

    def test_the_backup_runs_on_the_spring_change_day(self):
        elapses = _elapses("*-*-* 04:30:00", "2027-03-27 12:00:00 UTC")
        assert elapses[0] == ("2027-03-28 04:30:00", "EEST")
