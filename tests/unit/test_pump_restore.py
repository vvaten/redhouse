"""The boot restore applies the pump state the program says is due now."""

import datetime
import json
import os
import pathlib
import tempfile
from unittest.mock import Mock

import pytest

from src.control import pump_restore as pr
from src.control.hardware_implementations import MockHardwareInterface
from src.control.pump_controller import PumpController

HELSINKI = pr.HELSINKI
REPO = pathlib.Path(__file__).resolve().parents[2]


def _at(y, mo, d, h, mi=0):
    return int(HELSINKI.localize(datetime.datetime(y, mo, d, h, mi)).timestamp())


def _write(base, day, entries):
    program = {
        "version": "2.0.0",
        "program_date": day.isoformat(),
        "loads": {
            "geothermal_pump": {
                "schedule": [
                    {
                        "timestamp": ts,
                        "local_time": datetime.datetime.fromtimestamp(ts, HELSINKI).isoformat(),
                        "command": command,
                    }
                    for ts, command in entries
                ]
            }
        },
    }
    path = pr.program_path(day, str(base))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(program))


@pytest.fixture
def base():
    with tempfile.TemporaryDirectory() as tmp:
        yield pathlib.Path(tmp)


@pytest.fixture
def controller(base):
    return PumpController(
        hardware=MockHardwareInterface(), state_file=os.path.join(base, "pump_state.json")
    )


D11 = datetime.date(2026, 10, 11)
D12 = datetime.date(2026, 10, 12)


class TestDueCommand:
    def test_latest_entry_at_or_before_now(self, base):
        _write(base, D12, [(_at(2026, 10, 12, 0), "ON"), (_at(2026, 10, 12, 1, 30), "ALE")])
        assert pr.due_command(_at(2026, 10, 12, 1, 30), str(base))["command"] == "ALE"
        assert pr.due_command(_at(2026, 10, 12, 1, 29), str(base))["command"] == "ON"

    def test_future_entries_are_ignored(self, base):
        _write(base, D12, [(_at(2026, 10, 12, 5), "EVU")])
        _write(base, D11, [(_at(2026, 10, 11, 22), "ON")])
        assert pr.due_command(_at(2026, 10, 12, 0, 10), str(base))["command"] == "ON"

    def test_yesterday_counts_when_today_has_nothing_due(self, base):
        _write(base, D11, [(_at(2026, 10, 11, 23), "EVU")])
        assert pr.due_command(_at(2026, 10, 12, 0, 5), str(base))["command"] == "EVU"

    def test_no_program_means_nothing_due(self, base):
        assert pr.due_command(_at(2026, 10, 12, 9), str(base)) is None

    def test_a_malformed_program_is_ignored(self, base):
        """The restore must still fall back to the last known state."""
        _write(base, D11, [(_at(2026, 10, 11, 22), "ON")])
        _write(base, D12, [(_at(2026, 10, 12, 1), "OFF")])
        assert pr.due_command(_at(2026, 10, 12, 9), str(base))["command"] == "ON"

    def test_today_wins_a_tie_at_midnight(self, base):
        """Yesterday's file ends with the ALE at today's 00:00."""
        midnight = _at(2026, 10, 12, 0)
        _write(base, D11, [(_at(2026, 10, 11, 22), "ON"), (midnight, "ALE")])
        _write(base, D12, [(midnight, "EVU")])
        assert pr.due_command(midnight + 60, str(base))["command"] == "EVU"


class TestChooseCommand:
    def test_an_entry_newer_than_the_last_command_wins(self, base, controller):
        """The executor was down when the 12:00 EVU came due."""
        _write(base, D12, [(_at(2026, 10, 12, 12), "EVU")])
        controller.last_command, controller.last_command_time = "ON", _at(2026, 10, 12, 10)
        assert pr.choose_command(_at(2026, 10, 12, 14), str(base), controller, True) == "EVU"

    def test_a_newer_known_command_beats_an_older_entry(self, base, controller):
        """A manual override, or the executor ran a command the file lacks."""
        _write(base, D11, [(_at(2026, 10, 11, 23), "ALE")])
        controller.last_command, controller.last_command_time = "EVU", _at(2026, 10, 12, 13)
        assert pr.choose_command(_at(2026, 10, 12, 14), str(base), controller, True) == "EVU"

    def test_an_unsynced_clock_trusts_only_the_last_command(self, base, controller):
        _write(base, D12, [(_at(2026, 10, 12, 12), "EVU")])
        controller.last_command, controller.last_command_time = "ON", _at(2026, 10, 12, 10)
        assert pr.choose_command(_at(2026, 10, 12, 14), str(base), controller, False) == "ON"

    def test_a_clock_behind_the_last_command_is_not_trusted(self, base, controller):
        """fake-hwclock boots with the last saved time, before the outage."""
        _write(base, D12, [(_at(2026, 10, 12, 9), "ON")])
        controller.last_command, controller.last_command_time = "EVU", _at(2026, 10, 12, 12)
        assert pr.choose_command(_at(2026, 10, 12, 9, 17), str(base), controller, True) == "EVU"


class TestClock:
    def test_sync_state_is_read_from_timedatectl(self, monkeypatch):
        class Done:
            def __init__(self, out):
                self.stdout = out

        monkeypatch.setattr(pr.subprocess, "run", lambda *a, **k: Done("yes\n"))
        assert pr.clock_synchronized() is True
        monkeypatch.setattr(pr.subprocess, "run", lambda *a, **k: Done("no\n"))
        assert pr.clock_synchronized() is False

        def missing(*a, **k):
            raise OSError("no timedatectl")

        monkeypatch.setattr(pr.subprocess, "run", missing)
        assert pr.clock_synchronized() is False

    def test_wait_returns_when_synced_and_gives_up_at_the_deadline(self, monkeypatch):
        clock = {"t": 0.0}
        monkeypatch.setattr(pr.time, "monotonic", lambda: clock["t"])
        monkeypatch.setattr(pr.time, "sleep", lambda s: clock.update(t=clock["t"] + s))
        answers = iter([False, False, True])
        monkeypatch.setattr(pr, "clock_synchronized", lambda: next(answers))
        assert pr.wait_for_clock_sync(60) is True
        monkeypatch.setattr(pr, "clock_synchronized", lambda: False)
        clock["t"] = 0.0
        assert pr.wait_for_clock_sync(60) is False
        assert clock["t"] >= 60


class TestMainClockHandling:
    """The time used must be read after the wait: the boot clock is stale."""

    @pytest.fixture
    def fake(self, monkeypatch):
        state = {"now": 1000, "syncs": [], "restores": [], "timeouts": []}
        monkeypatch.setattr(
            pr, "PumpController", lambda dry_run: PumpController(hardware=MockHardwareInterface())
        )
        monkeypatch.setattr(pr.time, "time", lambda: state["now"])

        def wait(timeout):
            state["timeouts"].append(timeout)
            synced = state["syncs"].pop(0)
            state["now"] += 5000
            return synced

        def restore(now, base_dir, controller, clock_ok):
            state["restores"].append((now, clock_ok))
            controller.last_command = "ON"
            return 0

        monkeypatch.setattr(pr, "wait_for_clock_sync", wait)
        monkeypatch.setattr(pr, "restore", restore)
        return state

    def test_a_synced_clock_is_read_after_the_wait(self, fake):
        fake["syncs"] = [True]
        assert pr.main(["--dry-run"]) == 0
        assert fake["restores"] == [(6000, True)]

    def test_a_late_sync_rechecks_the_program(self, fake, monkeypatch):
        fake["syncs"] = [False, True]
        monkeypatch.setattr(pr, "choose_command", lambda *a: "ALE")
        assert pr.main(["--dry-run"]) == 0
        assert fake["restores"] == [(6000, False), (11000, True)]
        assert fake["timeouts"] == [pr.CLOCK_SYNC_TIMEOUT, pr.LATE_SYNC_TIMEOUT]

    def test_a_late_sync_that_agrees_applies_nothing_more(self, fake, monkeypatch):
        fake["syncs"] = [False, True]
        monkeypatch.setattr(pr, "choose_command", lambda *a: "ON")
        assert pr.main(["--dry-run"]) == 0
        assert fake["restores"] == [(6000, False)]

    def test_a_failed_unsynced_apply_is_retried_after_the_sync(self, fake, monkeypatch):
        """A Shelly that comes up with the router answers only later."""
        fake["syncs"] = [False, True]
        results = iter([1, 0])

        def restore(now, base_dir, controller, clock_ok):
            fake["restores"].append((now, clock_ok))
            controller.last_command = "ON"
            return next(results)

        monkeypatch.setattr(pr, "restore", restore)
        monkeypatch.setattr(pr, "choose_command", lambda *a: "ON")
        assert pr.main(["--dry-run"]) == 0
        assert fake["restores"] == [(6000, False), (11000, True)]

    def test_a_clock_that_never_syncs_fails_the_unit(self, fake):
        fake["syncs"] = [False, False]
        assert pr.main(["--dry-run"]) == 1
        assert fake["restores"] == [(6000, False)]


class TestLateSyncKeepsANewerManualCommand:
    """The unsynced apply must not hide that the manual EVU is newer."""

    def test_manual_evu_survives_a_late_sync_and_the_next_boot(self, base, controller, monkeypatch):
        _write(base, D12, [(_at(2026, 10, 12, 10), "ON")])
        controller.last_command, controller.last_command_time = "EVU", _at(2026, 10, 12, 12)
        waits = iter([(False, _at(2026, 10, 12, 9, 19)), (True, _at(2026, 10, 12, 14))])
        clock = {"now": _at(2026, 10, 12, 9, 17)}

        def wait(timeout):
            synced, clock["now"] = next(waits)
            return synced

        monkeypatch.setattr(pr, "PumpController", lambda dry_run: controller)
        monkeypatch.setattr(pr, "PROGRAM_BASE_DIR", str(base))
        monkeypatch.setattr(pr.time, "time", lambda: clock["now"])
        monkeypatch.setattr(pr, "wait_for_clock_sync", wait)
        assert pr.main(["--dry-run"]) == 0
        assert controller.hardware.commands_executed == ["EVU"]
        reloaded = PumpController(
            hardware=MockHardwareInterface(), state_file=str(controller.state_file)
        )
        assert reloaded.last_command == "EVU"
        assert reloaded.last_command_time == _at(2026, 10, 12, 12)


class TestMain:
    def test_dry_run_is_passed_to_the_controller(self, monkeypatch):
        seen = {}

        def fake_controller(dry_run):
            seen["dry_run"] = dry_run
            return PumpController(hardware=MockHardwareInterface())

        monkeypatch.setattr(pr, "PumpController", fake_controller)
        monkeypatch.setattr(pr, "wait_for_clock_sync", lambda timeout: True)
        monkeypatch.setattr(pr, "restore", lambda *args: 0)
        assert pr.main(["--dry-run"]) == 0
        assert seen["dry_run"] is True

    def test_mock_hardware_without_dry_run_is_refused(self, monkeypatch):
        monkeypatch.setattr(
            pr, "PumpController", lambda dry_run: PumpController(hardware=MockHardwareInterface())
        )
        monkeypatch.setattr(pr, "restore", lambda *args: 0)
        assert pr.main([]) == 1


class TestRestore:
    def test_on_gets_a_pulse_and_a_fresh_window(self, base, controller):
        _write(base, D12, [(_at(2026, 10, 12, 8), "ON")])
        now = _at(2026, 10, 12, 9)
        assert pr.restore(now, str(base), controller, True) == 0
        assert controller.hardware.commands_executed == ["EVU", "ON"]
        assert controller.last_command == "ON"
        assert controller.on_since == now + controller.EVU_CYCLE_DURATION

    def test_ale_gets_a_pulse_and_no_on_clock(self, base, controller):
        _write(base, D12, [(_at(2026, 10, 12, 8), "ALE")])
        assert pr.restore(_at(2026, 10, 12, 9), str(base), controller, True) == 0
        assert controller.hardware.commands_executed == ["EVU", "ALE"]
        assert controller.on_since is None

    def test_no_restore_switches_the_ventilation(self, base):
        """The hardware layer's circulation pump relay is the house ventilation."""
        for command, written in (("ON", ["EVU", "ON"]), ("ALE", ["EVU", "ALE"]), ("EVU", ["EVU"])):
            controller = PumpController(
                hardware=MockHardwareInterface(), state_file=os.path.join(base, f"{command}.json")
            )
            controller.hardware.control_circulation_pump = Mock(return_value=False)
            _write(base, D12, [(_at(2026, 10, 12, 8), command)])
            assert pr.restore(_at(2026, 10, 12, 9), str(base), controller, True) == 0, command
            assert controller.hardware.commands_executed == written, command
            controller.hardware.control_circulation_pump.assert_not_called()

    def test_without_a_program_the_last_known_state_is_reapplied(self, base, controller):
        controller.last_command = "ALE"
        assert pr.restore(_at(2026, 10, 12, 9), str(base), controller, True) == 0
        assert controller.hardware.commands_executed == ["EVU", "ALE"]

    def test_with_nothing_known_nothing_is_applied(self, base, controller):
        assert pr.restore(_at(2026, 10, 12, 9), str(base), controller, True) == 1
        assert controller.hardware.commands_executed == []

    def test_a_hardware_failure_fails_the_unit(self, base, controller):
        controller.hardware.command_success = False
        _write(base, D12, [(_at(2026, 10, 12, 8), "ON")])
        assert pr.restore(_at(2026, 10, 12, 9), str(base), controller, True) == 1

    def test_a_mode_write_failing_after_the_pulse_fails_the_unit(self, base, controller):
        """The pulse left the relays in EVU, so the state says EVU."""
        controller.hardware.write_pump_command = lambda command: command == "EVU"
        _write(base, D12, [(_at(2026, 10, 12, 8), "ON")])
        assert pr.restore(_at(2026, 10, 12, 9), str(base), controller, True) == 1
        assert controller.last_command == "EVU" and controller.on_since is None

    def test_state_survives_for_the_executor(self, base, controller):
        _write(base, D12, [(_at(2026, 10, 12, 8), "ON")])
        now = _at(2026, 10, 12, 9)
        pr.restore(now, str(base), controller, True)
        reloaded = PumpController(
            hardware=MockHardwareInterface(), state_file=str(controller.state_file)
        )
        assert reloaded.last_command == "ON"
        assert reloaded.on_since == now + controller.EVU_CYCLE_DURATION
