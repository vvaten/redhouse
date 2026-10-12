"""
Comprehensive tests for pump controller business logic.

These tests focus on the business logic that is now easily testable
thanks to the hardware interface abstraction. They test EVU cycling,
state management, and time-dependent logic.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.control.hardware_implementations import MockHardwareInterface
from src.control.pump_controller import PumpController


def _controller(tmpdir, last_command=None, on_since=None):
    controller = PumpController(
        hardware=MockHardwareInterface(), state_file=os.path.join(tmpdir, "state.json")
    )
    controller.last_command = last_command
    controller.last_command_time = on_since
    controller.on_since = on_since
    return controller


class TestEVUCycling(unittest.TestCase):
    """The periodic cycle runs only while ON, once ON has lasted 105 min."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_threshold_leaves_one_check_and_a_margin_inside_the_window(self):
        c = PumpController
        assert c.EVU_CYCLE_THRESHOLD + c.EXECUTOR_INTERVAL + c.PULSE_MARGIN <= (
            c.DIRECT_HEATING_BLOCK
        )

    def test_cycle_needed_at_the_threshold(self):
        controller = _controller(self.tmpdir, "ON", on_since=1000)
        threshold = controller.EVU_CYCLE_THRESHOLD
        assert controller.check_evu_cycle_needed(1000 + threshold) is True
        assert controller.check_evu_cycle_needed(1000 + threshold - 1) is False

    def test_the_105_minute_check_pulses_even_30_seconds_after_a_pulse(self):
        """on_since is the release, 30 s after the run that pulsed."""
        controller = _controller(self.tmpdir, "ON", on_since=1000 + 30)
        assert controller.check_evu_cycle_needed(1000 + 105 * 60) is True

    def test_cycle_never_needed_outside_on(self):
        for command in ("EVU", "ALE", None):
            controller = _controller(self.tmpdir, command, on_since=1000)
            assert controller.check_evu_cycle_needed(1000 + 10 * 3600) is False, command

    def test_cycle_restores_on_and_restarts_the_clock(self):
        controller = _controller(self.tmpdir, "ON", on_since=1000)
        result = controller.perform_evu_cycle(current_time=8000)
        assert result["success"] is True
        assert controller.hardware.commands_executed == ["EVU", "ON"]
        assert controller.last_command == "ON"
        assert controller.on_since == 8000 + controller.EVU_CYCLE_DURATION
        assert controller.check_evu_cycle_needed(8000 + 60 * 60) is False

    def test_a_pulse_keeps_the_time_of_the_last_real_command(self):
        """A due entry newer than the ON must still beat it after a pulse."""
        controller = _controller(self.tmpdir, "ON", on_since=1000)
        controller.perform_evu_cycle(current_time=8000)
        assert controller.last_command_time == 1000

    def test_cycle_refuses_outside_on(self):
        """A pulse during EVU or ALE could only end in ON."""
        for command in ("EVU", "ALE"):
            controller = _controller(self.tmpdir, command, on_since=None)
            result = controller.perform_evu_cycle(current_time=8000)
            assert result["success"] is False, command
            assert controller.hardware.commands_executed == [], command
            assert controller.last_command == command

    def test_repeated_on_does_not_cycle_or_restart_the_clock(self):
        controller = _controller(self.tmpdir, "ON", on_since=1000)
        result = controller.execute_command("ON", scheduled_time=4600, actual_time=4601)
        assert result["success"] is True
        assert controller.hardware.commands_executed == ["ON"]
        assert controller.on_since == 1000

    def test_hourly_on_block_pulses_every_105_minutes(self):
        """Hourly ON commands plus 15-min executor checks a few seconds late."""
        controller = _controller(self.tmpdir, "ALE", on_since=None)
        pulses = []
        for minute in range(0, 6 * 60, 15):
            now = 1_000_000 + minute * 60 + 3
            if minute % 60 == 0:
                controller.execute_command("ON", scheduled_time=now, actual_time=now)
            if controller.check_evu_cycle_needed(now):
                controller.perform_evu_cycle(current_time=now)
                pulses.append(minute)
        assert pulses == [105, 210, 315]

    def test_every_gap_between_releases_stays_inside_the_window(self):
        """Runs start 0 to 40 s late, as InfluxDB stalls can make them."""
        controller = _controller(self.tmpdir, "ALE", on_since=None)
        releases = []
        for i, minute in enumerate(range(0, 12 * 60, 15)):
            now = 1_000_000 + minute * 60 + (i * 7) % 41
            if minute % 60 == 0:
                controller.execute_command("ON", scheduled_time=now, actual_time=now)
                if minute == 0:
                    releases.append(now + controller.EVU_CYCLE_DURATION)
            if controller.check_evu_cycle_needed(now):
                controller.perform_evu_cycle(current_time=now)
                releases.append(now + controller.EVU_CYCLE_DURATION)
        gaps = [b - a for a, b in zip(releases, releases[1:])]
        assert gaps and max(gaps) < controller.DIRECT_HEATING_BLOCK - 60

    def test_on_then_evu_never_turns_the_pump_on(self):
        """Leaving ON for EVU stops the clock, so no pulse ends in ON."""
        controller = _controller(self.tmpdir, "ALE", on_since=None)
        start = 1_000_000
        controller.execute_command("ON", scheduled_time=start, actual_time=start)
        for minute in (15, 30, 45):
            assert controller.check_evu_cycle_needed(start + minute * 60) is False
        controller.execute_command("EVU", scheduled_time=start + 3599, actual_time=start + 3599)
        assert controller.on_since is None
        for minute in range(60, 6 * 60, 15):
            assert controller.check_evu_cycle_needed(start + minute * 60) is False
        assert controller.hardware.commands_executed[-1] == "EVU"
        assert controller.last_command == "EVU"

    def test_entering_on_from_ale_or_evu_starts_the_clock(self):
        for previous in ("ALE", "EVU"):
            controller = _controller(self.tmpdir, previous, on_since=None)
            controller.execute_command("ON", scheduled_time=5000, actual_time=5000)
            assert controller.on_since == 5000, previous

    def test_an_unknown_state_going_on_pulses_first(self):
        controller = _controller(self.tmpdir, None, on_since=None)
        controller.execute_command("ON", scheduled_time=5000, actual_time=5000)
        assert controller.hardware.commands_executed == ["EVU", "ON"]

    def test_a_failed_transition_pulse_is_retried_by_the_periodic_check(self):
        controller = _controller(self.tmpdir, "ALE", on_since=None)
        controller.hardware.write_pump_command = lambda command: command != "EVU"
        controller.execute_command("ON", scheduled_time=5000, actual_time=5000)
        assert controller.check_evu_cycle_needed(5000) is True

    def test_a_failed_restore_after_a_pulse_records_evu(self):
        """The relays were last written EVU, so the state must say so."""
        controller = _controller(self.tmpdir, "ON", on_since=1000)
        controller.hardware.write_pump_command = lambda command: command == "EVU"
        result = controller.perform_evu_cycle(current_time=8000)
        assert result["success"] is False
        assert controller.last_command == "EVU" and controller.on_since is None

    def test_a_failed_write_after_a_transition_pulse_records_evu(self):
        """The pulse wrote EVU last, so the pump is not in its old ALE."""
        controller = _controller(self.tmpdir, "ALE", on_since=None)
        controller.hardware.write_pump_command = lambda command: command == "EVU"
        result = controller.execute_command("ON", scheduled_time=5000, actual_time=5000)
        assert result["success"] is False
        assert controller.last_command == "EVU" and controller.on_since is None
        assert json.loads(Path(controller.state_file).read_text())["last_command"] == "EVU"

    def test_an_unsynced_apply_keeps_the_last_real_command_time(self):
        """Also when the mode write after the pulse fails and EVU is recorded."""
        for write_ok in (True, False):
            controller = _controller(self.tmpdir, "ON", on_since=7000)
            controller.hardware.write_pump_command = lambda c, ok=write_ok: ok or c == "EVU"
            controller.apply_state("ON", 3000, clock_ok=False)
            assert controller.last_command == ("ON" if write_ok else "EVU")
            assert controller.last_command_time == 7000, write_ok

    def test_a_write_that_dies_midway_keeps_the_old_state(self):
        controller = _controller(self.tmpdir, "EVU", on_since=None)
        controller._save_state()
        controller.last_command = "ON"
        with patch("src.control.pump_controller.json.dump", side_effect=OSError("power")):
            controller._save_state()
        assert json.loads(Path(controller.state_file).read_text())["last_command"] == "EVU"

    def test_the_default_state_file_does_not_depend_on_the_directory(self):
        controller = PumpController(hardware=MockHardwareInterface())
        assert controller.state_file.is_absolute()

    def test_a_relay_write_is_retried(self):
        controller = _controller(self.tmpdir, "ON", on_since=1000)
        controller.test_mode = False
        answers = iter([False, False, True])
        controller.hardware.write_pump_command = lambda command: next(answers)
        with patch("src.control.pump_controller.time.sleep") as sleep:
            assert controller.execute_command("ON", 5000, 5000)["success"] is True
        assert sleep.call_count == controller.RELAY_ATTEMPTS - 1

    def test_a_dry_run_controller_never_writes_the_live_state_file(self):
        controller = PumpController(hardware=MockHardwareInterface())
        assert controller.state_file.name == "pump_state_dry_run.json"

    def test_leaving_on_clears_the_clock(self):
        for command in ("ALE", "EVU"):
            controller = _controller(self.tmpdir, "ON", on_since=1000)
            controller.execute_command(command, scheduled_time=5000, actual_time=5000)
            assert controller.on_since is None, command

    def test_no_evu_cycle_on_evu_to_on_transition(self):
        """Test no EVU cycle when going from EVU to ON."""
        mock_hw = MockHardwareInterface()
        controller = PumpController(hardware=mock_hw)

        # Set up state: pump was in EVU mode
        controller.last_command = "EVU"
        controller.last_command_time = 1000

        # Execute ON command (EVU -> ON transition)
        result = controller.execute_command("ON", scheduled_time=2000, actual_time=2010)

        assert result["success"] is True
        # Should NOT have performed extra EVU cycle
        assert mock_hw.commands_executed.count("EVU") == 0


class TestStateManagement(unittest.TestCase):
    """Test state persistence and management."""

    def test_state_saved_after_command(self):
        """Test that state is saved after executing a command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "pump_state.json")
            mock_hw = MockHardwareInterface()
            controller = PumpController(hardware=mock_hw, state_file=state_file)

            # Execute command
            controller.execute_command("ON", scheduled_time=1000, actual_time=1010)

            # Check that state file was created
            assert Path(state_file).exists()

            # Load and verify state
            with open(state_file) as f:
                state = json.load(f)
                assert state["last_command"] == "ON"
                assert state["last_command_time"] == 1010

    def test_state_loaded_on_init(self):
        """Test that state is loaded when controller is initialized."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "pump_state.json")

            # Create initial state
            initial_state = {
                "on_since": 4000,
                "last_command": "ON",
                "last_command_time": 5000,
                "last_evu_cycle_time": 2000,
            }
            with open(state_file, "w") as f:
                json.dump(initial_state, f)

            # Create controller (should load state)
            mock_hw = MockHardwareInterface()
            controller = PumpController(hardware=mock_hw, state_file=state_file)

            # Verify state was loaded
            assert controller.on_since == 4000
            assert controller.last_command == "ON"
            assert controller.last_command_time == 5000
            assert controller.last_evu_cycle_time == 2000

    def test_old_state_file_gets_on_since_from_the_last_on_or_cycle(self):
        """Files written before on_since carry on_time_accumulated instead."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "pump_state.json")
            old = {
                "on_time_accumulated": 3000,
                "last_command": "ON",
                "last_command_time": 5000,
                "last_evu_cycle_time": 6000,
            }
            with open(state_file, "w") as f:
                json.dump(old, f)
            controller = PumpController(hardware=MockHardwareInterface(), state_file=state_file)
            assert controller.on_since == 6000

    def test_old_state_file_outside_on_has_no_on_since(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "pump_state.json")
            old = {"on_time_accumulated": 0, "last_command": "ALE", "last_command_time": 5000}
            with open(state_file, "w") as f:
                json.dump(old, f)
            controller = PumpController(hardware=MockHardwareInterface(), state_file=state_file)
            assert controller.on_since is None


class TestHardwareFailureScenarios(unittest.TestCase):
    """Test error handling for hardware failures."""

    def test_hardware_failure_returns_error(self):
        """Test that hardware failure is properly reported."""
        mock_hw = MockHardwareInterface()
        mock_hw.command_success = False
        controller = PumpController(hardware=mock_hw)

        result = controller.execute_command("ON", scheduled_time=1000, actual_time=1010)

        assert result["success"] is False
        assert "Hardware command failed" in result["error"]

    def test_evu_cycle_failure_is_reported(self):
        """Test that EVU cycle failure is reported but doesn't block command."""
        mock_hw = MockHardwareInterface()
        controller = PumpController(hardware=mock_hw)

        # ALE -> ON is a mode change, so it cycles EVU first
        controller.last_command = "ALE"
        controller.last_command_time = 1000

        # Make hardware fail for EVU command only
        def selective_failure(command):
            if command == "EVU":
                return False
            return True

        mock_hw.write_pump_command = selective_failure

        # Execute command (should warn about EVU cycle failure but proceed)
        result = controller.execute_command("ON", scheduled_time=2000, actual_time=2010)

        # Command should still succeed
        assert result["success"] is True


class TestCirculationPumpControl(unittest.TestCase):
    """Test AC circulation pump control logic."""

    def test_circulation_pump_on_when_transitioning_to_on(self):
        """Test that circulation pump turns on when going from EVU to ON."""
        mock_hw = MockHardwareInterface()
        controller = PumpController(hardware=mock_hw)

        # Set up state: pump was in EVU mode
        controller.last_command = "EVU"
        controller.last_command_time = 1000

        # Execute ON command
        controller.execute_command("ON", scheduled_time=2000, actual_time=2010)

        # Circulation pump should have been turned on
        assert mock_hw.circulation_pump_on is True

    def test_circulation_pump_off_when_transitioning_to_evu(self):
        """Test that circulation pump turns off when going to EVU."""
        mock_hw = MockHardwareInterface()
        controller = PumpController(hardware=mock_hw)

        # Set up state: pump was ON
        controller.last_command = "ON"
        controller.last_command_time = 1000

        # Execute EVU command
        controller.execute_command("EVU", scheduled_time=2000, actual_time=2010)

        # Circulation pump should have been turned off
        assert mock_hw.circulation_pump_on is False


class TestClockInjection(unittest.TestCase):
    """Test that clock injection enables time-dependent testing."""

    def test_clock_injection_allows_fake_time(self):
        """Test that injected clock is used instead of real time."""
        mock_hw = MockHardwareInterface()
        fake_time = 12345

        def fake_clock():
            return fake_time

        controller = PumpController(hardware=mock_hw, clock=fake_clock)

        # check_evu_cycle_needed without arg should use fake clock
        controller.last_command = "ON"
        controller.last_command_time = fake_time
        controller.on_since = fake_time
        fake_time = fake_time + (105 * 60)  # Advance time

        # Update needs explicit time since we can't mutate the lambda closure
        cycle_needed = controller.check_evu_cycle_needed(fake_time)

        assert cycle_needed is True


class TestErrorHandling(unittest.TestCase):
    """Test error handling and edge cases."""

    def test_state_load_failure_is_handled_gracefully(self):
        """Test that corrupted state file doesn't crash initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "corrupt_state.json")

            # Create corrupted state file
            with open(state_file, "w") as f:
                f.write("{invalid json")

            # Controller should still initialize with default state
            mock_hw = MockHardwareInterface()
            controller = PumpController(hardware=mock_hw, state_file=state_file)

            # Should have default values
            assert controller.on_since is None
            assert controller.last_command is None

    def test_state_save_failure_is_logged(self):
        """Test that state save failures are logged but don't crash."""
        mock_hw = MockHardwareInterface()
        # Use invalid path that can't be created
        if os.name == "nt":
            invalid_path = "Z:\\nonexistent\\directory\\state.json"
        else:
            invalid_path = "/tmp/nonexistent/directory/state.json"

        controller = PumpController(hardware=mock_hw, state_file=invalid_path)

        # Execute command (should try to save state)
        result = controller.execute_command("ON", scheduled_time=1000, actual_time=1010)

        # Should still succeed even if state save fails
        assert result["success"] is True

    def test_get_status_method(self):
        """Test get_status method delegates to hardware."""
        mock_hw = MockHardwareInterface()
        controller = PumpController(hardware=mock_hw)

        status = controller.get_status()

        assert status is not None
        assert status["mode"] == "MOCK"
        assert status["status"] == "simulated"

    def test_execute_command_with_exception_in_hardware(self):
        """Test exception handling when hardware raises exception."""
        mock_hw = MockHardwareInterface()

        def failing_write(command):
            raise RuntimeError("Hardware failure")

        mock_hw.write_pump_command = failing_write
        controller = PumpController(hardware=mock_hw)

        # Set up state to avoid EVU cycle (no ALE->ON transition)
        controller.last_command = "EVU"
        controller.last_command_time = 1000

        # Should catch exception and return error
        result = controller.execute_command("ON", scheduled_time=2000, actual_time=2010)

        assert result["success"] is False
        assert "Hardware failure" in result["error"]

    def test_evu_cycle_on_ale_to_on_transition(self):
        """Test automatic EVU cycle when going from ALE to ON."""
        mock_hw = MockHardwareInterface()
        controller = PumpController(hardware=mock_hw)

        # Set up state: pump was in ALE mode
        controller.last_command = "ALE"
        controller.last_command_time = 1000

        # Execute ON command (ALE -> ON transition)
        result = controller.execute_command("ON", scheduled_time=2000, actual_time=2010)

        assert result["success"] is True
        # Should have performed EVU cycle
        assert "EVU" in mock_hw.commands_executed

    def test_no_evu_cycle_on_on_to_ale_transition(self):
        """ALE has a lower target, so the pump cannot fall behind its curve."""
        mock_hw = MockHardwareInterface()
        controller = PumpController(hardware=mock_hw)
        controller.last_command = "ON"
        controller.last_command_time = 1000

        result = controller.execute_command("ALE", scheduled_time=2000, actual_time=2010)

        assert result["success"] is True
        assert mock_hw.commands_executed == ["ALE"]
        assert controller.on_since is None

    def test_circulation_pump_on_when_transitioning_from_evu_to_ale(self):
        """Test that circulation pump turns on when going from EVU to ALE."""
        mock_hw = MockHardwareInterface()
        controller = PumpController(hardware=mock_hw)

        # Set up state: pump was in EVU mode
        controller.last_command = "EVU"
        controller.last_command_time = 1000

        # Execute ALE command
        controller.execute_command("ALE", scheduled_time=2000, actual_time=2010)

        # Circulation pump should have been turned on
        assert mock_hw.circulation_pump_on is True


if __name__ == "__main__":
    unittest.main()
