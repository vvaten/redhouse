"""Tests for the load guard's override file."""

import json

from src.control import load_overrides as lo


def _write(path, entries):
    path.write_text(json.dumps({"overrides": entries}))


def test_a_written_override_is_read_back(tmp_path):
    path = tmp_path / "o.json"
    lo.write_overrides(path, [lo.Override(lo.GARAGE, "OFF", 100.0, 200.0, "cap_guard")], 150.0)
    assert lo.active_override(path, lo.GARAGE, 150.0).command == "OFF"
    assert lo.active_override(path, lo.GARAGE, 200.0) is None


def test_an_ended_override_stays_for_the_hand_back(tmp_path):
    """A guard write soon after a hold ended must not erase it."""
    path = tmp_path / "o.json"
    lo.write_overrides(path, [lo.Override(lo.PUMP, "ALE", 0.0, 100.0, "x", "ON")], 150.0)
    assert lo.ended_override(path, lo.PUMP, 150.0).restore_to == "ON"
    lo.write_overrides(path, lo.read_overrides(path), 100.0 + lo.RESTORE_WINDOW_S + 1)
    assert lo.read_overrides(path) == []


def test_a_bad_command_or_load_is_ignored(tmp_path):
    path = tmp_path / "o.json"
    _write(
        path,
        [
            {"load_id": lo.PUMP, "command": "ON", "since": 0, "until": 100, "reason": "x"},
            {"load_id": "car", "command": "OFF", "since": 0, "until": 100, "reason": "x"},
            {"load_id": lo.PUMP, "command": "EVU", "since": 0, "until": "soon", "reason": "x"},
        ],
    )
    assert lo.read_overrides(path) == []


def test_an_override_longer_than_two_hours_never_runs(tmp_path):
    path = tmp_path / "o.json"
    _write(path, [{"load_id": lo.PUMP, "command": "EVU", "since": 0, "until": 9000, "reason": "x"}])
    assert lo.active_override(path, lo.PUMP, 10) is None


def test_a_missing_or_broken_file_holds_nothing(tmp_path):
    assert lo.read_overrides(tmp_path / "none.json") == []
    (tmp_path / "bad.json").write_text("{trunc")
    assert lo.read_overrides(tmp_path / "bad.json") == []


def test_the_newest_running_override_wins(tmp_path):
    path = tmp_path / "o.json"
    _write(
        path,
        [
            {"load_id": lo.PUMP, "command": "ALE", "since": 0, "until": 100, "reason": "x"},
            {"load_id": lo.PUMP, "command": "EVU", "since": 10, "until": 100, "reason": "x"},
        ],
    )
    assert lo.active_override(path, lo.PUMP, 50).command == "EVU"
