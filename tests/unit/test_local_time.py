"""The program day is the Helsinki date, whatever the zone of the host. Under
freeze_time a naive now() gives UTC, as on a host on UTC; 21:30 UTC is
00:30 the next day in Helsinki."""

import datetime
import json

import pandas as pd
import pytest
from freezegun import freeze_time

from src.common.local_time import helsinki_date
from src.control.heating_optimizer import HeatingOptimizer
from src.control.program_executor import HeatingProgramExecutor
from src.control.program_generator import HeatingProgramGenerator

AFTER_MIDNIGHT = [
    ("2026-10-24 21:30:00", datetime.date(2026, 10, 25)),
    ("2027-03-27 22:30:00", datetime.date(2027, 3, 28)),
    ("2026-11-09 22:30:00", datetime.date(2026, 11, 10)),
]


@pytest.mark.parametrize("utc, day", AFTER_MIDNIGHT)
def test_helsinki_date_after_local_midnight(utc, day):
    with freeze_time(utc):
        assert helsinki_date() == day
        assert helsinki_date(1) == day + datetime.timedelta(days=1)
        assert helsinki_date(-1) == day - datetime.timedelta(days=1)


@pytest.mark.parametrize("utc, day", AFTER_MIDNIGHT)
def test_executor_loads_the_helsinki_day(utc, day, tmp_path):
    for d in (day - datetime.timedelta(days=1), day):
        folder = tmp_path / d.isoformat()[:7]
        folder.mkdir(exist_ok=True)
        program = {"version": "2.0.0", "loads": {}, "program_date": d.isoformat()}
        (folder / f"heating_program_schedule_{d.isoformat()}.json").write_text(json.dumps(program))

    executor = HeatingProgramExecutor.__new__(HeatingProgramExecutor)
    with freeze_time(utc):
        loaded = executor.load_program(base_dir=str(tmp_path))
    assert loaded["program_date"] == day.isoformat()


@pytest.mark.parametrize("utc, day", AFTER_MIDNIGHT)
def test_generator_dates_the_program_by_helsinki(utc, day):
    generator = HeatingProgramGenerator.__new__(HeatingProgramGenerator)
    with freeze_time(utc):
        program_date_str, _ = generator._calculate_program_date(None, 1)
    assert program_date_str == (day + datetime.timedelta(days=1)).isoformat()


@pytest.mark.parametrize("utc, day", AFTER_MIDNIGHT)
def test_optimizer_keeps_the_hours_of_the_helsinki_day(utc, day):
    start = pd.Timestamp(day.isoformat(), tz="Europe/Helsinki") - pd.Timedelta(days=1)
    index = pd.date_range(start, periods=72, freq="h")
    priorities = pd.DataFrame({"price_total": range(72)}, index=index)

    with freeze_time(utc):
        day_rows = HeatingOptimizer().filter_day_priorities(priorities, date_offset=0)

    assert {t.date() for t in day_rows.index} == {day}
