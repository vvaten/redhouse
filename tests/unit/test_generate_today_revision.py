"""The evening re-plan of today lands on a fresh read of today's file."""

import datetime
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import generate_heating_program_v2 as gen
from src.common.atomic_json import write_json
from src.control.quarter_program import MOVED_KEY, TodayPart
from src.monitoring.program_check import program_path

DAY = datetime.date(2026, 12, 1)
START = int(datetime.datetime(2026, 12, 1, 14, 15, tzinfo=datetime.timezone.utc).timestamp())


def _entry(t, command, minutes=None, **extra):
    return {
        "timestamp": t,
        "command": command,
        "duration_minutes": minutes,
        "local_time": "",
        **extra,
    }


def _program(entries):
    return {
        "version": "2.0.0",
        "program_date": DAY.isoformat(),
        "loads": {"geothermal_pump": {"schedule": entries}},
    }


def _generator(part, moved=0):
    return SimpleNamespace(today_part=TodayPart(part, START, DAY.isoformat(), moved))


def test_the_revision_keeps_what_the_executor_wrote_meanwhile(tmp_path):
    path = program_path(DAY, str(tmp_path))
    done = _entry(START - 3600, "ON", 30, executed_at=START - 3600)
    write_json(path, _program([done, _entry(START - 1800, "ALE"), _entry(START + 3600, "ON", 60)]))
    part = [_entry(START + 900, "ON", 15), _entry(START + 1800, "ALE")]
    with patch.object(gen.time, "time", return_value=START - 600):
        revision = gen._save_today_revision(_generator(part, moved=3), str(tmp_path))
    saved = json.loads(path.read_text())
    assert saved == revision and saved[MOVED_KEY] == 3
    schedule = saved["loads"]["geothermal_pump"]["schedule"]
    assert schedule[0]["executed_at"] == START - 3600
    assert [e["timestamp"] for e in schedule] == [
        START - 3600,
        START - 1800,
        START + 900,
        START + 1800,
    ]


def test_too_near_the_window_start_it_refuses_and_writes_nothing(tmp_path):
    path = program_path(DAY, str(tmp_path))
    write_json(path, _program([_entry(START + 3600, "ON", 60)]))
    before = path.read_text()
    with patch.object(gen.time, "time", return_value=START - 30):
        with pytest.raises(RuntimeError):
            gen._save_today_revision(_generator([]), str(tmp_path))
    assert path.read_text() == before


def test_without_a_re_plan_nothing_is_written(tmp_path):
    assert gen._save_today_revision(SimpleNamespace(today_part=None), str(tmp_path)) is None


def test_an_influx_failure_of_the_revision_is_only_logged():
    generator = MagicMock()
    generator.save_program_influxdb.side_effect = RuntimeError("down")
    gen._save_revision_influxdb(generator, {"program_date": "x"}, dry_run=False)
    gen._save_revision_influxdb(generator, None, dry_run=False)
    assert generator.save_program_influxdb.call_count == 1
