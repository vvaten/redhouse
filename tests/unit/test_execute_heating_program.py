"""Tests for the program of the day before in the executor wrapper."""

import datetime
import importlib.util
import json
import pathlib
import sys
from unittest.mock import Mock, patch

import pytest

from src.monitoring.program_check import program_path

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
PROGRAM = {"version": "2.0.0", "program_date": "2026-10-11", "loads": {}}


def _load_wrapper():
    path = REPO_ROOT / "execute_heating_program_v2.py"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wrapper = _load_wrapper()


def _write_previous(base, text):
    path = program_path(datetime.date(2026, 10, 11), str(base))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class TestLoadPreviousProgram:
    """A bad program of the day before must not stop today's run."""

    def test_loads_the_day_before(self, tmp_path):
        _write_previous(tmp_path, json.dumps(PROGRAM))
        assert wrapper._load_previous_program("2026-10-12", str(tmp_path)) == PROGRAM

    def test_missing_or_unusable_gives_none(self, tmp_path):
        assert wrapper._load_previous_program("2026-10-12", str(tmp_path)) is None
        for text in ("", "{trunc", "null", "[]", json.dumps({"version": "2.0.0"})):
            _write_previous(tmp_path, text)
            assert wrapper._load_previous_program("2026-10-12", str(tmp_path)) is None, text


class TestDayTransition:
    def _at(self, hour, minute):
        now = Mock()
        now.hour, now.minute = hour, minute
        clock = Mock()
        clock.datetime.now = Mock(return_value=now)
        return patch.object(wrapper, "datetime", clock)

    def test_merges_in_the_first_quarter_hour(self):
        executor = Mock()
        executor.handle_day_transition = Mock(return_value="merged")
        with self._at(0, 5):
            assert wrapper._handle_day_transition_if_needed(executor, "today", "y") == "merged"
        executor.handle_day_transition.assert_called_once_with("today", "y")

    def test_no_merge_without_the_previous_program_or_later(self):
        executor = Mock()
        with self._at(0, 5):
            assert wrapper._handle_day_transition_if_needed(executor, "today", None) == "today"
        with self._at(0, 15):
            assert wrapper._handle_day_transition_if_needed(executor, "today", "y") == "today"
        executor.handle_day_transition.assert_not_called()


class TestMainPassesThePreviousProgram:
    """The midnight pulse guard needs yesterday's program in every path."""

    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        _write_previous(tmp_path, json.dumps(PROGRAM))
        fake = Mock()
        fake.handle_day_transition = Mock(side_effect=lambda program, previous: program)
        fake.execute_program = Mock(
            return_value={
                "executed_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
                "next_execution_time": None,
            }
        )
        monkeypatch.setattr(wrapper, "HeatingProgramExecutor", lambda dry_run: fake)
        monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-10-12", "--base-dir", str(tmp_path)])
        return fake

    def test_to_the_program_run(self, executor):
        executor.load_program = Mock(return_value={"version": "2.0.0", "loads": {}})
        assert wrapper.main() == 0
        assert executor.execute_program.call_args.kwargs["previous_program"] == PROGRAM

    def test_to_the_pulse_check_without_a_program(self, executor):
        executor.load_program = Mock(side_effect=FileNotFoundError("missing"))
        assert wrapper.main() == 1
        assert executor.run_periodic_pulse.call_args.kwargs["previous_program"] == PROGRAM

    def test_to_the_pulse_check_with_a_broken_program(self, executor):
        """A broken file for today must still leave the pulse check running."""
        broken = [
            TypeError("null"),
            OSError("EIO from the SD card"),
            {"version": "2.0.0", "loads": {"x": {"schedule": None}}},
        ]
        for result in broken:
            executor.run_periodic_pulse.reset_mock()
            if isinstance(result, Exception):
                executor.load_program = Mock(side_effect=result)
            else:
                executor.load_program = Mock(return_value=result)
            assert wrapper.main() == 1
            executor.run_periodic_pulse.assert_called_once()
            executor.execute_program.assert_not_called()
