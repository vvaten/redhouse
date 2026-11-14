"""Tests for the control lock shared by the relay and pump writers."""

import itertools

import pytest

from src.common import control_lock as cl


def test_a_free_lock_is_taken_at_once(tmp_path):
    with cl.control_lock(0, tmp_path / "control.lock"):
        pass


def test_a_second_holder_times_out_while_the_first_holds_it(tmp_path):
    path = tmp_path / "control.lock"
    with cl.control_lock(0, path):
        with pytest.raises(cl.ControlLockTimeout):
            with cl.control_lock(0.5, path):
                pass


def test_the_lock_is_free_again_after_the_block(tmp_path):
    path = tmp_path / "control.lock"
    with cl.control_lock(0, path):
        pass
    with cl.control_lock(0, path):
        pass


def test_the_lock_is_released_when_the_block_raises(tmp_path):
    path = tmp_path / "control.lock"
    with pytest.raises(RuntimeError):
        with cl.control_lock(0, path):
            raise RuntimeError("boom")
    with cl.control_lock(0, path):
        pass


def test_the_default_path_follows_the_module_setting(tmp_path, monkeypatch):
    path = tmp_path / "sub" / "control.lock"
    monkeypatch.setattr(cl, "LOCK_FILE", path)
    with cl.control_lock(0):
        assert path.exists()


def test_a_long_wait_is_logged(tmp_path, monkeypatch, caplog):
    """A wait over a second shows in the log, so a slow holder is visible."""
    real_try = cl._try_lock
    tries = iter([lambda fd: False, real_try])
    monkeypatch.setattr(cl, "_try_lock", lambda fd: next(tries)(fd))
    clock = itertools.chain([0.0, 2.0], itertools.repeat(2.0))
    monkeypatch.setattr(cl.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(cl.time, "sleep", lambda s: None)
    with cl.control_lock(10, tmp_path / "control.lock"):
        pass
    assert "Waited 2.0 s" in caplog.text
