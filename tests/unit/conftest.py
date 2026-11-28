"""Shared fixtures for the unit tests."""

import types

import pytest


@pytest.fixture(autouse=True)
def _pump_state_in_tmp(tmp_path, monkeypatch):
    """Keep pump state files out of the data directory of the install."""
    monkeypatch.setattr("src.control.pump_controller.DATA_DIR", tmp_path / "data")
    monkeypatch.setattr("src.common.control_lock.LOCK_FILE", tmp_path / "data" / "control.lock")


@pytest.fixture(autouse=True)
def evu_releases(monkeypatch):
    """The EVU release points the pump controller would write, never sent."""
    releases: list = []

    def record(source, new_state, previous, via, now, mock):
        releases.append((source, new_state, previous, via))
        return True

    monkeypatch.setattr(
        "src.control.pump_controller.evu_release", types.SimpleNamespace(log_release=record)
    )
    return releases
