"""Unit tests for pump controller."""

import os
import unittest
from unittest.mock import patch

from src.control.hardware_implementations import MockHardwareInterface
from src.control.pump_controller import MultiLoadController, PumpController


class TestPumpController(unittest.TestCase):
    """Test cases for PumpController class."""

    def test_initialization(self):
        """Test controller initialization with mock hardware."""
        controller = PumpController(dry_run=True)
        self.assertIsInstance(controller.hardware, MockHardwareInterface)

    def test_valid_commands(self):
        """Test valid command validation."""
        controller = PumpController(dry_run=True)
        self.assertTrue(controller.validate_command("ON"))
        self.assertTrue(controller.validate_command("ALE"))
        self.assertTrue(controller.validate_command("EVU"))
        self.assertFalse(controller.validate_command("INVALID"))

    def test_execute_command_dry_run(self):
        """Test command execution with mock hardware."""
        mock_hw = MockHardwareInterface()
        controller = PumpController(hardware=mock_hw)
        result = controller.execute_command("ON", scheduled_time=1000, actual_time=1010)

        self.assertTrue(result["success"])
        self.assertEqual(result["command"], "ON")
        self.assertEqual(result["delay_seconds"], 10)
        # Check that command was recorded in mock hardware
        self.assertIn("ON", mock_hw.commands_executed)

    @patch.dict(os.environ, {"STAGING_MODE": "true"})
    def test_staging_mode_from_env(self):
        """Test that STAGING_MODE environment variable uses mock hardware."""
        controller = PumpController(dry_run=False)
        self.assertIsInstance(controller.hardware, MockHardwareInterface)

        result = controller.execute_command("ON", scheduled_time=1000, actual_time=1010)
        self.assertTrue(result["success"])

    def test_execute_command_invalid(self):
        """Test that invalid commands raise ValueError."""
        controller = PumpController(dry_run=True)

        with self.assertRaises(ValueError):
            controller.execute_command("INVALID", scheduled_time=1000, actual_time=1000)

    def test_execute_command_large_delay(self):
        """Test warning for large execution delays."""
        controller = PumpController(dry_run=True)

        # 2000 second delay (more than MAX_EXECUTION_DELAY)
        result = controller.execute_command("ON", scheduled_time=1000, actual_time=3000)

        self.assertTrue(result["success"])  # Still executes with mock hardware
        self.assertEqual(result["delay_seconds"], 2000)

    def test_execute_command_hardware_success(self):
        """Test successful hardware execution."""
        mock_hw = MockHardwareInterface()
        mock_hw.command_success = True
        controller = PumpController(hardware=mock_hw)

        result = controller.execute_command("ON", scheduled_time=1000, actual_time=1010)

        self.assertTrue(result["success"])
        self.assertIn("hardware interface", result["output"])
        self.assertIn("ON", mock_hw.commands_executed)

    def test_execute_command_hardware_failure(self):
        """Test failed hardware execution."""
        mock_hw = MockHardwareInterface()
        mock_hw.command_success = False
        controller = PumpController(hardware=mock_hw)

        result = controller.execute_command("ON", scheduled_time=1000, actual_time=1010)

        self.assertFalse(result["success"])
        self.assertIn("Hardware command failed", result["error"])


class TestMultiLoadController(unittest.TestCase):
    """Test cases for MultiLoadController class."""

    def test_initialization(self):
        """Test multi-load controller initialization."""
        controller = MultiLoadController(dry_run=True)
        self.assertTrue(controller.dry_run)
        self.assertIsNotNone(controller.pump_controller)

    def test_execute_geothermal_pump_command(self):
        """Test executing geothermal pump command."""
        controller = MultiLoadController(dry_run=True)
        result = controller.execute_load_command(
            "geothermal_pump", "ON", scheduled_time=1000, actual_time=1010
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["command"], "ON")

    def test_execute_unknown_load(self):
        """Test that unknown loads raise ValueError."""
        controller = MultiLoadController(dry_run=True)

        with self.assertRaises(ValueError):
            controller.execute_load_command(
                "unknown_load", "ON", scheduled_time=1000, actual_time=1000
            )

    def test_execute_garage_heater_not_implemented(self):
        """Test garage heater returns not implemented error."""
        controller = MultiLoadController(dry_run=True)
        result = controller.execute_load_command(
            "garage_heater", "ON", scheduled_time=1000, actual_time=1010
        )

        self.assertFalse(result["success"])
        self.assertIn("not implemented", result["error"])

    def test_execute_ev_charger_not_implemented(self):
        """Test EV charger returns not implemented error."""
        controller = MultiLoadController(dry_run=True)
        result = controller.execute_load_command(
            "ev_charger", "ON", scheduled_time=1000, actual_time=1010
        )

        self.assertFalse(result["success"])
        self.assertIn("not implemented", result["error"])


if __name__ == "__main__":
    unittest.main()
