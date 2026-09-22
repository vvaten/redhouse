"""Tests for the heating program presence check."""

import datetime
import inspect
import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from src.monitoring import program_check as pc


@pytest.fixture
def program_dir(tmp_path):
    return tmp_path


def _write_program(base, program_date):
    path = pc.program_path(program_date, str(base))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"program_date": program_date.isoformat()}), encoding="utf-8")
    return path


class TestProgramPath:
    def test_matches_the_executor_layout(self):
        path = pc.program_path(datetime.date(2026, 9, 22), "/opt/redhouse")
        assert path.as_posix().endswith("2026-09/heating_program_schedule_2026-09-22.json")

    def test_month_directory_comes_from_the_date(self):
        path = pc.program_path(datetime.date(2027, 1, 3), ".")
        assert "2027-01" in path.as_posix()


class TestTomorrowLocal:
    def test_rolls_over_a_month_end(self):
        now = pc.HELSINKI.localize(datetime.datetime(2026, 9, 30, 18, 0))
        assert pc.tomorrow_local(now) == datetime.date(2026, 10, 1)

    def test_rolls_over_a_year_end(self):
        now = pc.HELSINKI.localize(datetime.datetime(2026, 12, 31, 18, 0))
        assert pc.tomorrow_local(now) == datetime.date(2027, 1, 1)


def _hours(start: str, stop: str) -> float:
    a = datetime.datetime.fromisoformat(start)
    b = datetime.datetime.fromisoformat(stop)
    return (b - a).total_seconds() / 3600.0


class TestLocalDayBounds:
    """Both midnights must carry their own offset.

    Adding 24 h to the start keeps the start offset, which ends an hour
    early in October and an hour late in March. An earlier version of
    this class asserted both bounds shared one offset, which cemented
    that bug instead of catching it.
    """

    def test_summer_day_is_24_hours(self):
        start, stop = pc.local_day_bounds(datetime.date(2026, 7, 15))
        assert start.endswith("+03:00") and stop.endswith("+03:00")
        assert _hours(start, stop) == 24

    def test_winter_day_is_24_hours(self):
        start, stop = pc.local_day_bounds(datetime.date(2026, 1, 15))
        assert start.endswith("+02:00") and stop.endswith("+02:00")
        assert _hours(start, stop) == 24

    def test_clocks_back_gives_a_25_hour_day(self):
        """2026-10-25: offset changes inside the day, so bounds differ."""
        start, stop = pc.local_day_bounds(datetime.date(2026, 10, 25))
        assert start.endswith("+03:00")
        assert stop.endswith("+02:00")
        assert _hours(start, stop) == 25

    def test_clocks_forward_gives_a_23_hour_day(self):
        """2026-03-29: the local day is an hour short."""
        start, stop = pc.local_day_bounds(datetime.date(2026, 3, 29))
        assert start.endswith("+02:00")
        assert stop.endswith("+03:00")
        assert _hours(start, stop) == 23

    def test_bounds_are_consecutive_local_midnights(self):
        start, stop = pc.local_day_bounds(datetime.date(2026, 9, 22))
        assert start.startswith("2026-09-22T00:00:00")
        assert stop.startswith("2026-09-23T00:00:00")


class TestCheckProgramExists:
    def test_present_file_is_found(self, program_dir):
        day = datetime.date(2026, 9, 22)
        _write_program(program_dir, day)
        assert pc.check_program_exists(day, str(program_dir)) is True

    def test_missing_file_is_reported(self, program_dir):
        assert pc.check_program_exists(datetime.date(2026, 9, 22), str(program_dir)) is False

    def test_a_directory_is_not_a_program(self, program_dir):
        """is_file, not exists: a stray directory must not pass."""
        day = datetime.date(2026, 9, 22)
        path = pc.program_path(day, str(program_dir))
        path.mkdir(parents=True)
        assert pc.check_program_exists(day, str(program_dir)) is False


class TestMain:
    @pytest.fixture(autouse=True)
    def _no_journal(self):
        """Otherwise each case spawns journalctl with a 30 s timeout."""
        with patch.object(pc, "generate_service_result", return_value="(journal)"):
            yield

    @pytest.fixture(autouse=True)
    def _base_dir(self, program_dir):
        with patch.object(pc, "PROGRAM_BASE_DIR", str(program_dir)):
            yield

    def _config(self, base_dir):
        config = MagicMock()
        values = {"RESEND_API_KEY": "key", "ALERT_EMAIL_TO": "to@example.com"}
        config.get.side_effect = lambda k, d=None: values.get(k, d)
        return config

    def test_present_program_sends_nothing(self, program_dir):
        day = datetime.date(2026, 9, 22)
        _write_program(program_dir, day)
        with patch.object(pc, "get_config", return_value=self._config(program_dir)):
            with patch.object(pc, "tomorrow_local", return_value=day):
                with patch.object(pc, "send_alert_email") as mail:
                    assert pc.main() == 0
        mail.assert_not_called()

    def test_missing_program_sends_one_mail(self, program_dir):
        day = datetime.date(2026, 9, 22)
        with patch.object(pc, "get_config", return_value=self._config(program_dir)):
            with patch.object(pc, "tomorrow_local", return_value=day):
                with patch.object(pc, "InfluxClient", side_effect=Exception("down")):
                    with patch.object(pc, "send_alert_email", return_value=True) as mail:
                        assert pc.main() == 0
        mail.assert_called_once()
        assert "2026-09-22" in mail.call_args.kwargs["subject"]

    def test_exits_zero_when_missing_but_mailed(self, program_dir):
        """The mail is the signal; a failed unit would only add noise."""
        day = datetime.date(2026, 9, 22)
        with patch.object(pc, "get_config", return_value=self._config(program_dir)):
            with patch.object(pc, "tomorrow_local", return_value=day):
                with patch.object(pc, "InfluxClient", side_effect=Exception("down")):
                    with patch.object(pc, "send_alert_email", return_value=True):
                        assert pc.main() == 0

    def test_influx_failure_does_not_stop_the_mail(self, program_dir):
        day = datetime.date(2026, 9, 22)
        with patch.object(pc, "get_config", return_value=self._config(program_dir)):
            with patch.object(pc, "tomorrow_local", return_value=day):
                with patch.object(pc, "InfluxClient", side_effect=Exception("down")):
                    with patch.object(pc, "send_alert_email", return_value=True) as mail:
                        pc.main()
        assert "unknown" in mail.call_args.kwargs["body"]

    def test_failed_send_exits_one(self, program_dir):
        """A Resend outage must not look like a clean run."""
        day = datetime.date(2026, 9, 22)
        with patch.object(pc, "get_config", return_value=self._config(program_dir)):
            with patch.object(pc, "tomorrow_local", return_value=day):
                with patch.object(pc, "InfluxClient", side_effect=Exception("down")):
                    with patch.object(pc, "send_alert_email", return_value=False):
                        assert pc.main() == 1

    def test_unconfigured_email_exits_one(self, program_dir):
        """No way to report the miss is a failure of this job."""
        config = MagicMock()
        config.get.side_effect = lambda k, d=None: d
        with patch.object(pc, "get_config", return_value=config):
            with patch.object(pc, "tomorrow_local", return_value=datetime.date(2026, 9, 22)):
                with patch.object(pc, "send_alert_email") as mail:
                    assert pc.main() == 1
        mail.assert_not_called()


class TestBuildBody:
    def test_names_the_date_and_the_path(self):
        day = datetime.date(2026, 9, 22)
        path = pc.program_path(day, "/opt/redhouse")
        with patch.object(pc, "generate_service_result", return_value="(journal)"):
            body = pc.build_body(day, path, 0)
        assert "2026-09-22" in body
        assert "heating_program_schedule_2026-09-22.json" in body
        assert "load_control points for that day: 0" in body

    def test_unknown_count_when_influx_unreachable(self):
        day = datetime.date(2026, 9, 22)
        with patch.object(pc, "generate_service_result", return_value="(journal)"):
            body = pc.build_body(day, pc.program_path(day, "."), None)
        assert "load_control points for that day: unknown" in body


class TestGeneratorUnit:
    """A staging install must quote its own journal, not production's."""

    def test_production_unit(self):
        with patch.object(pc, "_is_staging", return_value=False):
            assert pc.generator_unit() == "redhouse-generate-program.service"

    def test_staging_unit(self):
        with patch.object(pc, "_is_staging", return_value=True):
            assert pc.generator_unit() == "redhouse-staging-generate-program.service"


class TestBodyDoesNotPromiseAFallback:
    """Part 2 is not built: the executor has no fallback yet."""

    def test_says_there_is_no_fallback(self):
        day = datetime.date(2026, 9, 22)
        with patch.object(pc, "generate_service_result", return_value="(journal)"):
            body = pc.build_body(day, pc.program_path(day, "."), 0)
        assert "NO fallback" in body
        assert "falls back to the latest program at midnight" not in body


class TestBaseDirCannotDriftFromTheExecutor:
    """A checker looking elsewhere than the executor mails false alarms."""

    def test_matches_load_program_default(self):
        from src.control.program_executor import HeatingProgramExecutor

        sig = inspect.signature(HeatingProgramExecutor.load_program)
        assert pc.PROGRAM_BASE_DIR == sig.parameters["base_dir"].default

    def test_no_config_key_shadows_it(self):
        """A config key here would invent a disagreement with the writer."""
        source = pathlib.Path(pc.__file__).read_text(encoding="utf-8")
        assert "PROGRAM_OUTPUT_DIR" not in source
