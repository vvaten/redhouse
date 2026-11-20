"""Tests for the run deadline."""

import logging
import threading
from unittest.mock import Mock

from src.common import watchdog
from src.common.watchdog import Watchdog


def test_an_expired_run_reports_its_stage(caplog):
    fired = threading.Event()
    seen = []

    def expire(stage):
        seen.append(stage)
        fired.set()

    dog = Watchdog(0.05, on_expire=expire).start()
    dog.mark("3EM read")
    assert fired.wait(2)
    assert seen == ["3EM read"]
    assert "stuck in: 3EM read" in caplog.text


def test_the_report_goes_to_the_given_logger():
    fired = threading.Event()
    log = logging.getLogger("watchdog_test")
    log.error = Mock()
    Watchdog(0.05, on_expire=lambda stage: fired.set(), log=log).start()
    assert fired.wait(2)
    assert "stuck in: start" in log.error.call_args.args[0]


def test_a_hung_log_write_still_ends_the_run(monkeypatch):
    """The backstop expires the run although the log call never returns."""
    monkeypatch.setattr(watchdog, "BACKSTOP_S", 0.05)
    hang = threading.Event()
    fired = threading.Event()
    log = logging.getLogger("watchdog_hang")
    log.error = Mock(side_effect=lambda msg: hang.wait(5))
    Watchdog(0.05, on_expire=lambda stage: fired.set(), log=log).start()
    assert fired.wait(2)
    hang.set()


def test_a_finished_run_cancels_the_deadline():
    seen = []
    dog = Watchdog(0.05, on_expire=seen.append).start()
    dog.cancel()
    threading.Event().wait(0.2)
    assert seen == []
