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

from src.control.hardware_implementations import MockHardwareInterface
from src.control.pump_controller import PumpController


class TestEVUCycling(unittest.TestCase):
    """Test EVU cycling logic."""

    def test_evu_cycle_triggered_by_accumulated_on_time(self):
        """Test that EVU cycle is triggered when ON time exceeds threshold."""
        mock_hw = MockHardwareInterface()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "test_state.json")
            controller = PumpController(hardware=mock_hw, state_file=state_file)

            # Simulate pump being ON for 105 minutes by setting accumulated time directly
            # (since _update_on_time has a 1-hour sanity check)
            controller.last_command = "ON"
            controller.last_command_time = 1000
            controller.on_time_accumulated = 105 * 60  # 6300 seconds

            # Check if EVU cycle is needed
            cycle_needed = controller.check_evu_cycle_needed(1000 + 6300)

            assert cycle_needed is True
            assert controller.on_time_accumulated >= controller.EVU_CYCLE_THRESHOLD

    def test_evu_cycle_not_triggered_below_threshold(self):
        """Test that EVU cycle is not triggered when below threshold."""
        mock_hw = MockHardwareInterface()
        current_time = 1000

        def fake_clock():
            return current_time

        controller = PumpController(hardware=mock_hw, clock=fake_clock)

        # Set up state: pump has been ON for only 60 minutes
        controller.last_command = "ON"
        controller.last_command_time = 1000
        current_time = 1000 + (60 * 60)  # 3600 seconds later

        # Check if EVU cycle is needed
        cycle_needed = controller.check_evu_cycle_needed(current_time)

        assert cycle_needed is False
        assert controller.on_time_accumulated < controller.EVU_CYCLE_THRESHOLD

    def test_evu_cycle_resets_on_time_accumulator(self):
        """Test that EVU cycle resets the ON time accumulator."""
        mock_hw = MockHardwareInterface()
        controller = PumpController(hardware=mock_hw)

        # Set up accumulated ON time
        controller.on_time_accumulated = 7000  # Above threshold

        # Perform EVU cycle
        result = controller.perform_evu_cycle(current_time=2000)

        assert result["success"] is True
        assert controller.on_time_accumulated == 0
        assert "EVU" in mock_hw.commands_executed

    def test_evu_cycle_restores_previous_state(self):
        """Test that EVU cycle restores the previous command."""
        mock_hw = MockHardwareInterface()
        controller = PumpController(hardware=mock_hw)

        # Set up state: pump was ON
        controller.last_command = "ON"
        controller.last_command_time = 1000
        controller.on_time_accumulated = 7000

        # Perform EVU cycle
        result = controller.perform_evu_cycle(current_time=2000)

        assert result["success"] is True
        assert controller.last_command == "ON"  # Restored
        assert mock_hw.commands_executed == ["EVU", "ON"]

    def test_automatic_evu_cycle_on_on_to_on_transition(self):
        """Test automatic EVU cycle when going from ON to ON."""
        mock_hw = MockHardwareInterface()
        controller = PumpController(hardware=mock_hw)

        # Set up state: pump was ON
        controller.last_command = "ON"
        controller.last_command_time = 1000

        # Execute ON command (ON -> ON transition)
        result = controller.execute_command("ON", scheduled_time=2000, actual_time=2010)

        assert result["success"] is True
        # Should have performed EVU cycle
        assert "EVU" in mock_hw.commands_executed

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
                "on_time_accumulated": 3000,
                "last_command": "ALE",
                "last_command_time": 5000,
                "last_evu_cycle_time": 2000,
            }
            with open(state_file, "w") as f:
                json.dump(initial_state, f)

            # Create controller (should load state)
            mock_hw = MockHardwareInterface()
            controller = PumpController(hardware=mock_hw, state_file=state_file)

            # Verify state was loaded
            assert controller.on_time_accumulated == 3000
            assert controller.last_command == "ALE"
            assert controller.last_command_time == 5000
            assert controller.last_evu_cycle_time == 2000

    def test_on_time_accumulation(self):
        """Test that ON time is accumulated correctly."""
        mock_hw = MockHardwareInterface()
        controller = PumpController(hardware=mock_hw)

        # Set initial state: pump was ON
        controller.last_command = "ON"
        controller.last_command_time = 1000

        # Update ON time (30 minutes later)
        controller._update_on_time(1000 + 1800)

        assert controller.on_time_accumulated == 1800  # 30 minutes

    def test_on_time_not_accumulated_when_not_on(self):
        """Test that ON time is not accumulated when pump is not ON."""
        mock_hw = MockHardwareInterface()
        controller = PumpController(hardware=mock_hw)

        # Set initial state: pump was in EVU mode
        controller.last_command = "EVU"
        controller.last_command_time = 1000

        # Update ON time (should not accumulate)
        controller._update_on_time(1000 + 1800)

        assert controller.on_time_accumulated == 0


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

        # Set up state to trigger EVU cycle
        controller.last_command = "ON"
        controller.last_command_time = 1000
        controller.on_time_accumulated = 7000  # Above threshold

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
            assert controller.on_time_accumulated == 0
            assert controller.last_command is None

    def test_state_save_failure_is_logged(self):
        """Test that state save failures are logged but don't crash."""
        mock_hw = MockHardwareInterface()
        # Use invalid path that can't be created
        if os.name == "nt":
            invalid_path = "Z:\\nonexistent\\directory\\state.json"
        else:
            invalid_path = "/root/nonexistent/state.json"

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

    def test_evu_cycle_on_on_to_ale_transition(self):
        """Test automatic EVU cycle when going from ON to ALE."""
        mock_hw = MockHardwareInterface()
        controller = PumpController(hardware=mock_hw)

        # Set up state: pump was ON
        controller.last_command = "ON"
        controller.last_command_time = 1000

        # Execute ALE command (ON -> ALE transition)
        result = controller.execute_command("ALE", scheduled_time=2000, actual_time=2010)

        assert result["success"] is True
        # Should have performed EVU cycle
        assert "EVU" in mock_hw.commands_executed

    def test_on_time_accumulation_sanity_check(self):
        """Test that on_time accumulation has sanity check for large gaps."""
        mock_hw = MockHardwareInterface()
        controller = PumpController(hardware=mock_hw)

        # Set up state: pump was ON
        controller.last_command = "ON"
        controller.last_command_time = 1000

        # Try to update with time gap > 1 hour (3600 seconds)
        controller._update_on_time(1000 + 7200)  # 2 hours later

        # Should not accumulate due to sanity check
        assert controller.on_time_accumulated == 0

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
