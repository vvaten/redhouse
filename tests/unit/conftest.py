"""Shared fixtures for the unit tests."""

import pytest

from src.control import pump_controller


@pytest.fixture(autouse=True)
def _pump_state_in_tmp(tmp_path, monkeypatch):
    """Keep pump state files out of the data directory of the install."""
    monkeypatch.setattr(pump_controller, "DATA_DIR", tmp_path / "data")
