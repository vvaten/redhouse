"""The pre-pulse: a due EVU pulse in the minute before an ON quarter, released
back to ALE, so the ON itself needs no pulse."""

import pytest

from src.control import pre_pulse
from src.control.hardware_implementations import MockHardwareInterface
from src.control.pump_controller import PumpController

ON_AT = 1_000_800
NOW = ON_AT - 30
OLD = NOW - PumpController.PULSE_DUE_AFTER


def _program(*entries):
    schedule = [{"timestamp": t, "local_time": "x", "command": c} for t, c in entries]
    return {"version": "2.0.0", "loads": {"geothermal_pump": {"schedule": schedule}}}


def _pump(tmp_path, last_command="ALE", released=OLD):
    pump = PumpController(hardware=MockHardwareInterface(), state_file=str(tmp_path / "s.json"))
    pump.last_command = last_command
    pump.last_release_time = released
    return pump


def _free(at):
    return False


def _run(pump, programs, held=_free, now=NOW):
    return pre_pulse.run_pre_pulse(pump, programs, now, held)


PROGRAM = _program((ON_AT - 900, "ALE"), (ON_AT, "ON"), (ON_AT + 900, "ALE"))


class TestWhenItFires:
    def test_a_due_pulse_before_an_on_releases_to_ale(self, tmp_path, evu_releases):
        pump = _pump(tmp_path)
        assert _run(pump, [PROGRAM]) == "pulsed"
        assert pump.hardware.commands_executed == ["EVU", "ALE"]
        assert pump.last_command == "ALE"
        assert pump.last_release_time == NOW + pump.EVU_CYCLE_DURATION
        assert evu_releases == [("pulse", "ALE", "ALE", "pre_pulse")]

    def test_an_unknown_pump_state_pulses_too(self, tmp_path):
        pump = _pump(tmp_path, last_command=None, released=None)
        assert _run(pump, [PROGRAM]) == "pulsed"
        assert pump.last_command == "ALE"

    def test_an_on_at_midnight_comes_from_tomorrows_program(self, tmp_path):
        today = _program((ON_AT - 900, "ALE"))
        tomorrow = _program((ON_AT, "ON"))
        assert _run(_pump(tmp_path), [today, tomorrow]) == "pulsed"

    @pytest.mark.parametrize(
        "program",
        [
            _program((ON_AT - 900, "ALE"), (ON_AT, "ALE")),
            _program((ON_AT - 900, "ALE"), (ON_AT, "EVU")),
            _program((ON_AT - 900, "ALE"), (ON_AT + 60, "ON")),
            _program((ON_AT - 900, "ALE")),
        ],
        ids=["next is ALE", "next is EVU", "ON 90 s away", "no next entry"],
    )
    def test_not_without_an_on_within_a_minute(self, tmp_path, program):
        pump = _pump(tmp_path)
        assert _run(pump, [program]) == "skipped"
        assert pump.hardware.commands_executed == []

    def test_an_executed_on_is_not_the_next_entry(self, tmp_path):
        program = _program((ON_AT - 900, "ALE"), (ON_AT, "ON"))
        program["loads"]["geothermal_pump"]["schedule"][1]["executed_at"] = NOW
        assert _run(_pump(tmp_path), [program]) == "skipped"

    @pytest.mark.parametrize("state", ["ON", "EVU"])
    def test_not_from_on_or_evu(self, tmp_path, state):
        pump = _pump(tmp_path, last_command=state)
        assert _run(pump, [PROGRAM]) == "skipped"
        assert pump.hardware.commands_executed == []

    def test_not_while_the_last_release_is_fresh(self, tmp_path):
        pump = _pump(tmp_path, released=NOW - 60 * 60)
        assert _run(pump, [PROGRAM]) == "skipped"
        assert pump.hardware.commands_executed == []

    def test_due_is_judged_at_the_on_not_now(self, tmp_path):
        """Due at the ON 30 s from now, so the ON would pulse: pulse now."""
        pump = _pump(tmp_path, released=ON_AT - PumpController.PULSE_DUE_AFTER)
        assert _run(pump, [PROGRAM]) == "pulsed"

    def test_not_inside_a_program_evu_block(self, tmp_path):
        program = _program((ON_AT - 900, "EVU"), (ON_AT, "ON"))
        pump = _pump(tmp_path, last_command=None, released=None)
        assert _run(pump, [program]) == "skipped"
        assert pump.hardware.commands_executed == []


class TestHolds:
    def test_a_load_guard_hold_now_blocks_it(self, tmp_path):
        pump = _pump(tmp_path)
        assert _run(pump, [PROGRAM], held=lambda at: at == NOW) == "skipped"
        assert pump.hardware.commands_executed == []

    def test_a_hold_at_the_on_blocks_it(self, tmp_path):
        pump = _pump(tmp_path)
        assert _run(pump, [PROGRAM], held=lambda at: at == ON_AT) == "skipped"
        assert pump.hardware.commands_executed == []


class TestTheOnAfterIt:
    def test_after_a_pre_pulse_the_on_skips_its_pulse(self, tmp_path):
        pump = _pump(tmp_path)
        _run(pump, [PROGRAM])
        pump.hardware.commands_executed.clear()
        assert pump.execute_command("ON", ON_AT, ON_AT + 3)["success"]
        assert pump.hardware.commands_executed == ["ON"]

    def test_a_failed_pre_pulse_leaves_the_pulse_to_the_on(self, tmp_path):
        pump = _pump(tmp_path)
        pump.hardware.write_pump_command = lambda command: False
        assert _run(pump, [PROGRAM]) == "failed"
        assert pump.last_command == "ALE" and pump.last_release_time == OLD
        written = []
        pump.hardware.write_pump_command = lambda command: written.append(command) or True
        assert pump.execute_command("ON", ON_AT, ON_AT + 3)["success"]
        assert written == ["EVU", "ON"]

    def test_a_failed_release_to_ale_records_evu(self, tmp_path, evu_releases):
        """The ON then leaves EVU, which is itself a release."""
        pump = _pump(tmp_path)
        pump.hardware.write_pump_command = lambda command: command == "EVU"
        assert _run(pump, [PROGRAM]) == "failed"
        assert pump.last_command == "EVU" and evu_releases == []
        pump.hardware.write_pump_command = lambda command: True
        assert pump.execute_command("ON", ON_AT, ON_AT + 3)["success"]
        assert pump.last_release_time == ON_AT + 3


def test_the_pump_refuses_a_pre_pulse_from_on(tmp_path):
    pump = _pump(tmp_path, last_command="ON")
    assert pump.pre_pulse(NOW)["success"] is False
    assert pump.hardware.commands_executed == []
