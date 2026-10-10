"""The fallback copies a program forward so the executor always has one."""

import datetime
import importlib.util
import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from src.control import program_fallback as pf

HELSINKI = pf.HELSINKI
REPO = pathlib.Path(__file__).resolve().parents[2]

D9 = datetime.date(2026, 10, 9)
D10 = datetime.date(2026, 10, 10)
D11 = datetime.date(2026, 10, 11)


def _local(y, mo, d, h, mi=0, is_dst=None):
    return HELSINKI.localize(datetime.datetime(y, mo, d, h, mi), is_dst=is_dst)


def _entry(when: datetime.datetime, command: str = "ON", executed: bool = True) -> dict:
    entry = {
        "timestamp": int(when.timestamp()),
        "utc_time": when.astimezone(datetime.timezone.utc).isoformat(),
        "local_time": when.isoformat(),
        "command": command,
        "duration_minutes": 60,
        "reason": "cheap_electricity",
    }
    if executed:
        entry["executed_at"] = entry["timestamp"] + 3
        entry["execution_result"] = {"success": True}
    return entry


def _program(day: datetime.date, entries: list) -> dict:
    return {
        "version": "2.0.0",
        "program_date": day.isoformat(),
        "loads": {"geothermal_pump": {"power_kw": 3.0, "schedule": entries}},
        "execution_status": {
            "executed_intervals": 3,
            "pending_intervals": 5,
            "last_executed_timestamp": 1,
            "next_execution_timestamp": 2,
        },
    }


def _day_program(day: datetime.date) -> dict:
    return _program(day, [_entry(_local(day.year, day.month, day.day, 1))])


NOW = _local(2026, 10, 10, 23, 50)


def _schedule(program):
    return program["loads"]["geothermal_pump"]["schedule"]


class TestTargetDates:
    @pytest.mark.parametrize(
        "hour, minute, expected",
        [
            (0, 20, [D10]),
            (14, 0, [D10]),
            (23, 10, [D10]),
            (23, 30, [D10, D11]),
            (23, 50, [D10, D11]),
        ],
    )
    def test_today_always_tomorrow_after_the_last_retry(self, hour, minute, expected):
        assert pf.target_dates(_local(2026, 10, 10, hour, minute)) == expected


class TestCopyProgramForward:
    def test_moves_one_day_and_clears_execution(self):
        source = _day_program(D10)
        copied = pf.copy_program_forward(source, D11, NOW)
        (entry,) = _schedule(copied)
        assert copied["program_date"] == "2026-10-11"
        assert entry["local_time"] == "2026-10-11T01:00:00+03:00"
        assert entry["utc_time"] == "2026-10-10T22:00:00+00:00"
        assert entry["timestamp"] == _schedule(source)[0]["timestamp"] + 86400
        assert "executed_at" not in entry and "execution_result" not in entry
        assert entry["command"] == "ON" and entry["reason"] == "cheap_electricity"
        assert copied["execution_status"]["executed_intervals"] == 0
        assert copied["execution_status"]["last_executed_timestamp"] is None

    def test_source_is_not_changed(self):
        source = _day_program(D10)
        before = json.dumps(source, sort_keys=True)
        pf.copy_program_forward(source, D11, NOW)
        assert json.dumps(source, sort_keys=True) == before

    def test_records_where_it_came_from(self):
        source = _day_program(D10)
        copied = pf.copy_program_forward(source, D11, NOW)
        assert pf.is_fallback_copy(copied) and not pf.is_fallback_copy(source)
        assert copied["fallback"]["copied_from"] == "2026-10-10"
        assert copied["fallback"]["days_in_a_row"] == 1
        again = pf.copy_program_forward(copied, datetime.date(2026, 10, 12), NOW)
        assert again["fallback"]["days_in_a_row"] == 2
        assert again["fallback"]["copied_from"] == "2026-10-11"

    def test_entries_merged_from_the_day_before_are_dropped(self):
        """The executor merges unexecuted D-1 entries into D at midnight."""
        merged = _entry(_local(2026, 10, 9, 23, 45), "EVU", executed=False)
        own = _entry(_local(2026, 10, 10, 6), "ON")
        ale = _entry(_local(2026, 10, 11, 0, 0), "ALE", executed=False)
        copied = pf.copy_program_forward(_program(D10, [merged, own, ale]), D11, NOW)
        assert [e["command"] for e in _schedule(copied)] == ["ON", "ALE"]
        assert _schedule(copied)[1]["local_time"] == "2026-10-12T00:00:00+03:00"

    def test_a_time_written_in_another_offset_moves_24_hours(self):
        """EVU and ALE times carry the host's offset, not always Helsinki's."""
        utc = datetime.datetime(2026, 10, 10, 21, 0, tzinfo=datetime.timezone.utc)
        source = _program(D10, [_entry(utc, "ALE", executed=False)])
        source["loads"]["geothermal_pump"]["schedule"][0]["local_time"] = utc.isoformat()
        (entry,) = _schedule(pf.copy_program_forward(source, D11, NOW))
        assert entry["local_time"] == "2026-10-12T00:00:00+03:00"
        assert entry["timestamp"] == int(utc.timestamp()) + 86400

    def test_into_the_long_october_day_keeps_the_wall_clock(self):
        source = _program(datetime.date(2026, 10, 24), [_entry(_local(2026, 10, 24, 12))])
        copied = pf.copy_program_forward(source, datetime.date(2026, 10, 25), NOW)
        (entry,) = _schedule(copied)
        assert entry["local_time"] == "2026-10-25T12:00:00+02:00"
        assert entry["timestamp"] - _schedule(source)[0]["timestamp"] == 25 * 3600

    def test_the_repeated_october_hour_takes_its_first_occurrence(self):
        source = _program(datetime.date(2026, 10, 24), [_entry(_local(2026, 10, 24, 3, 30))])
        copied = pf.copy_program_forward(source, datetime.date(2026, 10, 25), NOW)
        assert _schedule(copied)[0]["local_time"] == "2026-10-25T03:30:00+03:00"

    def test_copying_the_long_october_day_keeps_the_later_twin(self):
        first = _entry(_local(2026, 10, 25, 3, 15, is_dst=True), "ON")
        second = _entry(_local(2026, 10, 25, 3, 15, is_dst=False), "EVU")
        source = _program(datetime.date(2026, 10, 25), [first, second])
        copied = pf.copy_program_forward(source, datetime.date(2026, 10, 26), NOW)
        (entry,) = _schedule(copied)
        assert entry["command"] == "EVU"
        assert entry["local_time"] == "2026-10-26T03:15:00+02:00"

    def test_the_missing_march_hour_moves_forward_and_loses_only_itself(self):
        """03:30 does not exist on 2026-03-29; it lands on 04:30, which wins."""
        source = _program(
            datetime.date(2026, 3, 28),
            [_entry(_local(2026, 3, 28, h, 30), f"C{h}") for h in (1, 2, 3, 4, 5)],
        )
        copied = pf.copy_program_forward(source, datetime.date(2026, 3, 29), NOW)
        schedule = _schedule(copied)
        assert [e["command"] for e in schedule] == ["C1", "C2", "C4", "C5"]
        assert [e["local_time"][11:16] for e in schedule] == ["01:30", "02:30", "04:30", "05:30"]
        stamps = [e["timestamp"] for e in schedule]
        assert stamps == sorted(stamps)


class TestLoadValidProgram:
    def test_missing_corrupt_and_incomplete_are_none(self, tmp_path):
        path = tmp_path / "p.json"
        assert pf.load_valid_program(path) is None
        path.write_text("{ not json")
        assert pf.load_valid_program(path) is None
        path.write_text(json.dumps({"version": "2.0.0"}))
        assert pf.load_valid_program(path) is None
        path.write_text(json.dumps([1, 2]))
        assert pf.load_valid_program(path) is None
        path.write_text(json.dumps({"version": "2.0.0", "loads": []}))
        assert pf.load_valid_program(path) is None

    def test_a_usable_program_is_returned(self, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(json.dumps(_day_program(D10)))
        assert pf.load_valid_program(path)["program_date"] == "2026-10-10"


class TestWriteProgram:
    def test_a_failed_replace_leaves_no_temp_file(self, tmp_path):
        path = tmp_path / "2026-10" / "p.json"
        with patch.object(pf.os, "replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                pf.write_program(_day_program(D10), path)
        assert not list(path.parent.iterdir())


class TestEnsureProgram:
    @pytest.fixture(autouse=True)
    def _no_journal(self):
        with patch.object(pf, "generate_service_result", return_value="(journal)"):
            yield

    def _config(self, values=None):
        if values is None:
            values = {"RESEND_API_KEY": "key", "ALERT_EMAIL_TO": "to@example.com"}
        config = MagicMock()
        config.get.side_effect = lambda k: values[k] if k in values else None
        return config

    def _write(self, base: pathlib.Path, program: dict) -> pathlib.Path:
        path = pf.program_path(datetime.date.fromisoformat(program["program_date"]), str(base))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(program))
        return path

    def _path(self, base, day):
        return pf.program_path(day, str(base))

    def _subjects(self, mail):
        return [c.args[2] for c in mail.call_args_list]

    def test_tomorrow_is_not_judged_before_the_last_retry(self, tmp_path):
        self._write(tmp_path, _day_program(D10))
        with patch.object(pf, "send_alert_email") as mail:
            assert pf.ensure_program(_local(2026, 10, 10, 18), str(tmp_path), self._config()) == 0
        mail.assert_not_called()
        assert not self._path(tmp_path, D11).exists()

    def test_today_is_repaired_in_the_afternoon(self, tmp_path):
        """A Pi down from the afternoon before until 13:00 has no program today."""
        self._write(tmp_path, _day_program(D9))
        with patch.object(pf, "send_alert_email", return_value=True) as mail:
            pf.ensure_program(_local(2026, 10, 10, 14), str(tmp_path), self._config())
        assert pf.load_valid_program(self._path(tmp_path, D10))["fallback"]["copied_from"] == (
            "2026-10-09"
        )
        assert "WARNING" in self._subjects(mail)[0]

    def test_present_programs_are_left_alone(self, tmp_path):
        self._write(tmp_path, _day_program(D10))
        path = self._write(tmp_path, _day_program(D11))
        before = path.read_text()
        with patch.object(pf, "send_alert_email") as mail:
            assert pf.ensure_program(NOW, str(tmp_path), self._config()) == 0
        mail.assert_not_called()
        assert path.read_text() == before

    def test_missing_tomorrow_is_copied_and_mailed(self, tmp_path):
        self._write(tmp_path, _day_program(D10))
        with patch.object(pf, "send_alert_email", return_value=True) as mail:
            assert pf.ensure_program(NOW, str(tmp_path), self._config()) == 0
        copied = json.loads(self._path(tmp_path, D11).read_text())
        assert copied["program_date"] == "2026-10-11"
        assert "version" in copied and "loads" in copied
        (subject,) = self._subjects(mail)
        assert "WARNING" in subject and "2026-10-11" in subject
        assert not list(self._path(tmp_path, D11).parent.glob("*.tmp"))

    def test_two_missing_days_are_chained(self, tmp_path):
        self._write(tmp_path, _day_program(D9))
        with patch.object(pf, "send_alert_email", return_value=True) as mail:
            pf.ensure_program(NOW, str(tmp_path), self._config())
        tomorrow = pf.load_valid_program(self._path(tmp_path, D11))
        assert tomorrow["fallback"]["days_in_a_row"] == 2
        assert len(self._subjects(mail)) == 2

    def test_a_corrupt_target_is_replaced(self, tmp_path):
        self._write(tmp_path, _day_program(D10))
        path = self._path(tmp_path, D11)
        path.write_text('{"version": "2.0.0", "lo')
        with patch.object(pf, "send_alert_email", return_value=True):
            pf.ensure_program(NOW, str(tmp_path), self._config())
        assert pf.load_valid_program(path)["program_date"] == "2026-10-11"

    def test_nothing_to_copy_is_critical(self, tmp_path):
        with patch.object(pf, "send_alert_email", return_value=True) as mail:
            assert pf.ensure_program(NOW, str(tmp_path), self._config()) == 0
        subjects = self._subjects(mail)
        assert subjects and all("CRITICAL" in s for s in subjects)
        assert not self._path(tmp_path, D11).exists()

    def test_a_corrupt_source_is_critical(self, tmp_path):
        source = self._write(tmp_path, _day_program(D10))
        self._write(tmp_path, _day_program(D10))
        source.write_text("garbage")
        self._write(tmp_path, _day_program(D9))
        with patch.object(pf, "send_alert_email", return_value=True) as mail:
            pf.ensure_program(_local(2026, 10, 11, 9), str(tmp_path), self._config())
        assert "CRITICAL" in self._subjects(mail)[0]

    def test_a_failed_write_is_critical(self, tmp_path):
        self._write(tmp_path, _day_program(D10))
        with patch.object(pf, "write_program", side_effect=OSError("disk full")):
            with patch.object(pf, "send_alert_email", return_value=True) as mail:
                assert pf.ensure_program(NOW, str(tmp_path), self._config()) == 0
        (subject,) = self._subjects(mail)
        assert "CRITICAL" in subject
        assert "disk full" in mail.call_args.args[3]

    def test_failed_send_exits_one(self, tmp_path):
        self._write(tmp_path, _day_program(D10))
        with patch.object(pf, "send_alert_email", return_value=False):
            assert pf.ensure_program(NOW, str(tmp_path), self._config()) == 1
        assert self._path(tmp_path, D11).is_file()

    def test_unconfigured_email_exits_one(self, tmp_path):
        with patch.object(pf, "send_alert_email") as mail:
            assert pf.ensure_program(NOW, str(tmp_path), self._config({})) == 1
        mail.assert_not_called()

    def test_sender_address_is_passed_when_configured(self, tmp_path):
        values = {"RESEND_API_KEY": "k", "ALERT_EMAIL_TO": "t", "ALERT_EMAIL_FROM": "f"}
        with patch.object(pf, "send_alert_email", return_value=True) as mail:
            pf.ensure_program(NOW, str(tmp_path), self._config(values))
        assert mail.call_args.kwargs["from_email"] == "f"


class TestGeneratorSkipsWhatItMade:
    @pytest.fixture
    def script(self):
        path = REPO / "generate_heating_program_v2.py"
        spec = importlib.util.spec_from_file_location("generate_under_test", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _tomorrow(self):
        return (datetime.datetime.now() + datetime.timedelta(days=1)).date()

    def _write(self, base, program):
        path = pf.program_path(self._tomorrow(), str(base))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(program))
        return path

    def test_missing_means_generate(self, script, tmp_path):
        assert script.generated_program_exists(1, str(tmp_path)) is False

    def test_generated_means_skip(self, script, tmp_path):
        self._write(tmp_path, _program(self._tomorrow(), []))
        assert script.generated_program_exists(1, str(tmp_path)) is True

    def test_a_fallback_copy_is_replaced(self, script, tmp_path):
        program = _program(self._tomorrow(), [])
        program["fallback"] = {"copied_from": "x", "copied_at": "y", "days_in_a_row": 1}
        self._write(tmp_path, program)
        assert script.generated_program_exists(1, str(tmp_path)) is False

    def test_an_unusable_file_is_replaced(self, script, tmp_path):
        self._write(tmp_path, _program(self._tomorrow(), [])).write_text("{")
        assert script.generated_program_exists(1, str(tmp_path)) is False

    def test_a_plan_missing_from_influx_is_retried(self, script, tmp_path):
        program = _program(self._tomorrow(), [])
        program[script.PLAN_NOT_IN_INFLUX_KEY] = True
        self._write(tmp_path, program)
        assert script.generated_program_exists(1, str(tmp_path)) is False

    def test_influx_failure_still_saves_the_json_marked(self, script, tmp_path):
        generator = MagicMock()
        generator.save_program_influxdb.side_effect = RuntimeError("write timeout")
        program = _program(self._tomorrow(), [])
        with pytest.raises(RuntimeError):
            script._save_program_files(generator, program, str(tmp_path), dry_run=False)
        saved = generator.save_program_json.call_args.args[0]
        assert saved[script.PLAN_NOT_IN_INFLUX_KEY] is True

    def test_success_saves_the_json_unmarked(self, script, tmp_path):
        generator = MagicMock()
        generator.save_program_json.return_value = "path"
        program = _program(self._tomorrow(), [])
        script._save_program_files(generator, program, str(tmp_path), dry_run=False)
        assert script.PLAN_NOT_IN_INFLUX_KEY not in generator.save_program_json.call_args.args[0]
