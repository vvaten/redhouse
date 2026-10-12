"""Shared fixtures for the unit tests."""

import pytest


@pytest.fixture(autouse=True)
def _pump_state_in_tmp(tmp_path, monkeypatch):
    """Keep pump state files out of the data directory of the install."""
    monkeypatch.setattr("src.control.pump_controller.DATA_DIR", tmp_path / "data")
