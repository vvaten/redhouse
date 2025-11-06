#!/usr/bin/env python
"""Heat pump controller - safe interface to MLP I2C control."""

import json
import os
import time
from pathlib import Path
from typing import Optional

import requests

from src.common.logger import setup_logger

logger = setup_logger(__name__)

# Try to import smbus2 for I2C communication
try:
    from smbus2 import SMBus

    I2C_AVAILABLE = True
except ImportError:
    I2C_AVAILABLE = False
    logger.warning("smbus2 not available - I2C control will use dry-run mode")


class PumpController:
    """
    Safe interface to geothermal heat pump control via I2C.

    Controls the heat pump through mlp_control.sh script which
    communicates via I2C bus with the pump controller.

    Commands:
    - ON: Force heating on
    - ALE: Automatic mode (pump decides)
    - EVU: EVU-OFF mode (blocks expensive direct heating)
    """

    # Valid pump commands
    VALID_COMMANDS = {"ON", "ALE", "EVU"}

    # Maximum delay to execute a command (seconds)
    MAX_EXECUTION_DELAY = 1800  # 30 minutes

    # EVU-OFF cycling parameters (smart cycling to prevent direct heating mode)
    # Pump switches to direct heating if EVU-OFF stays disabled for 120 minutes
    # Strategy: Cycle EVU-OFF (1) when turning pump ON, and (2) every 105min while ON
    EVU_CYCLE_THRESHOLD = 105 * 60  # 1h45min = 6300 seconds (15min safety margin)
    EVU_CYCLE_DURATION = 30  # Cycle EVU-OFF for 30 seconds

    # I2C configuration
    I2C_BUS = 1
    I2C_ADDRESS = 0x10
    I2C_REG1 = 0x01
    I2C_REG2 = 0x02

    # I2C command values
    I2C_COMMANDS = {
        "ON": (0x00, 0x00),  # Normal heating mode
        "ALE": (0xFF, 0x00),  # Lower temperature mode
        "EVU": (0xFF, 0xFF),  # EVU-OFF (pump disabled)
    }

    # AC pump (Shelly relay) configuration
    SHELLY_RELAY_URL = "http://192.168.1.5/relay/0"

    def __init__(
        self,
        dry_run: bool = False,
        state_file: str = None,
        shelly_url: str = None,
    ):
        """
        Initialize pump controller with direct I2C control.

        Args:
            dry_run: If True, log commands but don't execute (also enabled by STAGING_MODE env var)
            state_file: Path to state file (default: data/pump_state.json)
            shelly_url: Shelly relay URL (default: http://192.168.1.5/relay/0)
        """
        # Determine operating mode from environment
        staging_mode = os.getenv("STAGING_MODE", "false").lower() in ("true", "1", "yes")
        test_mode = os.getenv("TEST_MODE", "false").lower() in ("true", "1", "yes")

        # dry_run: Don't execute hardware commands (staging or test mode)
        # test_mode: Also skip delays for fast unit tests
        self.dry_run = dry_run or not I2C_AVAILABLE or staging_mode or test_mode
        self.test_mode = test_mode
        self.staging_mode = staging_mode
        self.state_file = Path(state_file or "data/pump_state.json")
        self.shelly_url = shelly_url or self.SHELLY_RELAY_URL

        # EVU cycle state tracking
        self.on_time_accumulated = 0  # Total ON time since last EVU cycle (seconds)
        self.last_command = None
        self.last_command_time = None
        self.last_evu_cycle_time = None

        # Load saved state
        self._load_state()

        # Log initialization mode
        if self.staging_mode:
            logger.info("PumpController initialized in STAGING mode (no hardware control, realistic timing)")
        elif self.test_mode:
            logger.info("PumpController initialized in TEST mode (no hardware, no delays)")
        elif self.dry_run:
            logger.info("PumpController initialized in DRY-RUN mode")
        else:
            logger.info("PumpController initialized in PRODUCTION mode with I2C direct control")

    def _load_state(self):
        """Load EVU cycle state from file."""
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    state = json.load(f)
                    self.on_time_accumulated = state.get("on_time_accumulated", 0)
                    self.last_command = state.get("last_command")
                    self.last_command_time = state.get("last_command_time")
                    self.last_evu_cycle_time = state.get("last_evu_cycle_time")
                    logger.info(
                        f"Loaded pump state: ON time={self.on_time_accumulated}s, "
                        f"last_command={self.last_command}"
                    )
            except Exception as e:
                logger.warning(f"Failed to load pump state: {e}")

    def _save_state(self):
        """Save EVU cycle state to file."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "on_time_accumulated": self.on_time_accumulated,
                "last_command": self.last_command,
                "last_command_time": self.last_command_time,
                "last_evu_cycle_time": self.last_evu_cycle_time,
            }
            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save pump state: {e}")

    def _update_on_time(self, current_time: int):
        """
        Update accumulated ON time based on last command.

        Args:
            current_time: Current time (epoch seconds)
        """
        if self.last_command == "ON" and self.last_command_time:
            elapsed = current_time - self.last_command_time
            if elapsed > 0 and elapsed < 3600:  # Sanity check: max 1 hour between updates
                self.on_time_accumulated += elapsed
                logger.debug(
                    f"Updated ON time: +{elapsed}s, total={self.on_time_accumulated}s "
                    f"(threshold={self.EVU_CYCLE_THRESHOLD}s)"
                )

    def check_evu_cycle_needed(self, current_time: int = None) -> bool:
        """
        Check if EVU-OFF cycle is needed to prevent direct heating mode.

        The pump switches to direct heating if EVU-OFF signal stays disabled
        for 120 minutes while pump is ON. We cycle at 105min with 15min safety margin.

        Args:
            current_time: Current time (epoch seconds), defaults to now

        Returns:
            True if EVU cycle should be performed
        """
        if current_time is None:
            current_time = int(time.time())

        # Update accumulated ON time
        self._update_on_time(current_time)

        if self.on_time_accumulated >= self.EVU_CYCLE_THRESHOLD:
            logger.info(
                f"EVU cycle needed: ON time {self.on_time_accumulated}s "
                f">= threshold {self.EVU_CYCLE_THRESHOLD}s"
            )
            return True

        return False

    def _control_ac_pump(self, turn_on: bool) -> bool:
        """
        Control AC pump via Shelly relay.

        Args:
            turn_on: True to turn on, False to turn off

        Returns:
            True if successful
        """
        action = "on" if turn_on else "off"
        url = f"{self.shelly_url}?turn={action}"

        if self.dry_run:
            logger.info(f"DRY-RUN: Would control AC pump: {action}")
            return True

        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                logger.info(f"AC pump turned {action}")
                return True
            else:
                logger.error(f"Failed to control AC pump: HTTP {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Failed to control AC pump: {e}")
            return False

    def _write_i2c(self, reg1_value: int, reg2_value: int) -> bool:
        """
        Write values to I2C pump controller.

        Args:
            reg1_value: Value for register 0x01
            reg2_value: Value for register 0x02

        Returns:
            True if successful
        """
        if self.dry_run:
            logger.info(f"DRY-RUN: Would write I2C: 0x{reg1_value:02X}, 0x{reg2_value:02X}")
            return True

        try:
            with SMBus(self.I2C_BUS) as bus:
                bus.write_byte_data(self.I2C_ADDRESS, self.I2C_REG1, reg1_value)
                bus.write_byte_data(self.I2C_ADDRESS, self.I2C_REG2, reg2_value)
            logger.debug(f"I2C write successful: 0x{reg1_value:02X}, 0x{reg2_value:02X}")
            return True
        except Exception as e:
            logger.error(f"I2C write failed: {e}")
            return False

    def _execute_raw_command(self, command: str) -> dict:
        """
        Execute a raw pump command without state tracking or EVU cycling.
        Used internally for EVU cycling to avoid recursion.

        Args:
            command: Command to execute (ON/ALE/EVU)

        Returns:
            Dict with success, output, error
        """
        result = {"success": False, "output": "", "error": None}

        if command not in self.I2C_COMMANDS:
            result["error"] = f"Unknown command: {command}"
            return result

        # Get I2C values for command
        reg1, reg2 = self.I2C_COMMANDS[command]

        if self.dry_run:
            result["success"] = True
            result["output"] = f"DRY-RUN: {command} (0x{reg1:02X}, 0x{reg2:02X})"
            return result

        # Write to I2C
        if not self._write_i2c(reg1, reg2):
            result["error"] = "I2C write failed"
            return result

        result["success"] = True
        result["output"] = f"{command} executed successfully"
        logger.info(f"Pump command {command} executed via I2C")

        return result

    def _perform_evu_cycle_internal(self, current_time: int) -> dict:
        """
        Internal EVU cycle that doesn't call execute_command (avoids recursion).
        Used when automatically cycling before turning pump ON.

        Args:
            current_time: Current time (epoch seconds)

        Returns:
            Dict with cycle results
        """
        logger.info(f"Performing EVU cycle (ON time was {self.on_time_accumulated}s)")

        result = {"success": False, "on_time_before": self.on_time_accumulated}

        # Step 1: Enable EVU-OFF
        evu_result = self._execute_raw_command("EVU")
        logger.info(f"EVU-OFF command: {evu_result['output']}")
        if not evu_result["success"]:
            result["error"] = f"Failed to enable EVU: {evu_result.get('error')}"
            return result

        # Step 2: Wait 30 seconds (skip only in test mode, not in staging)
        if self.test_mode:
            logger.info(f"TEST MODE: Skipping {self.EVU_CYCLE_DURATION}s wait for EVU cycle")
        else:
            logger.info(f"Waiting {self.EVU_CYCLE_DURATION}s for EVU cycle...")
            time.sleep(self.EVU_CYCLE_DURATION)

        # Step 3: Reset tracking
        self.on_time_accumulated = 0
        self.last_evu_cycle_time = current_time
        self._save_state()

        result["success"] = True
        logger.info("EVU cycle completed, ON time reset to 0")
        return result

    def perform_evu_cycle(self, current_time: int = None) -> dict:
        """
        Perform EVU-OFF cycle to reset pump's direct heating timer.

        This is the public API for manual EVU cycling (e.g., periodic check).
        Temporarily enables EVU-OFF for 30 seconds, then returns to previous state.

        Args:
            current_time: Current time (epoch seconds), defaults to now

        Returns:
            Dict with cycle results
        """
        if current_time is None:
            current_time = int(time.time())

        logger.info(
            f"Performing EVU cycle: ON time was {self.on_time_accumulated}s "
            f"(threshold={self.EVU_CYCLE_THRESHOLD}s)"
        )

        result = {
            "success": False,
            "on_time_before": self.on_time_accumulated,
            "cycle_time": current_time,
            "previous_command": self.last_command,
        }

        # Step 1: Enable EVU-OFF
        evu_result = self._execute_raw_command("EVU")
        logger.info(f"EVU-OFF command: {evu_result['output']}")
        if not evu_result["success"]:
            result["error"] = f"Failed to enable EVU: {evu_result.get('error')}"
            logger.error(result["error"])
            return result

        # Step 2: Wait 30 seconds (skip only in test mode, not in staging)
        if self.test_mode:
            logger.info(f"TEST MODE: Skipping {self.EVU_CYCLE_DURATION}s wait for EVU cycle")
        else:
            logger.info(f"Waiting {self.EVU_CYCLE_DURATION}s for EVU cycle...")
            time.sleep(self.EVU_CYCLE_DURATION)

        # Step 3: Return to previous state (or ON if unknown)
        restore_command = self.last_command if self.last_command in {"ON", "ALE"} else "ON"
        restore_result = self._execute_raw_command(restore_command)
        logger.info(f"Restore to {restore_command}: {restore_result['output']}")

        if not restore_result["success"]:
            result["error"] = f"Failed to restore {restore_command}: {restore_result.get('error')}"
            logger.error(result["error"])
            return result

        # Step 4: Update state
        self.last_command = restore_command
        self.last_command_time = current_time + self.EVU_CYCLE_DURATION
        self.on_time_accumulated = 0
        self.last_evu_cycle_time = current_time
        self._save_state()

        result["success"] = True
        result["restored_command"] = restore_command
        logger.info("EVU cycle completed successfully, ON time reset to 0")

        return result

    def execute_command(self, command: str, scheduled_time: int, actual_time: int) -> dict:
        """
        Execute a pump control command.

        Args:
            command: Command to execute (ON/ALE/EVU)
            scheduled_time: When command was scheduled (epoch)
            actual_time: When command is being executed (epoch)

        Returns:
            Dict with execution results:
            {
                "success": bool,
                "command": str,
                "scheduled_time": int,
                "actual_time": int,
                "delay_seconds": int,
                "output": str,
                "error": str or None
            }

        Raises:
            ValueError: If command is invalid
        """
        # Validate command
        if command not in self.VALID_COMMANDS:
            raise ValueError(
                f"Invalid command '{command}'. Must be one of: {', '.join(self.VALID_COMMANDS)}"
            )

        delay = actual_time - scheduled_time

        # Check if delay is too large
        if delay > self.MAX_EXECUTION_DELAY:
            logger.warning(
                f"Command '{command}' delayed by {delay}s (max: {self.MAX_EXECUTION_DELAY}s)"
            )

        result = {
            "success": False,
            "command": command,
            "scheduled_time": scheduled_time,
            "actual_time": actual_time,
            "delay_seconds": delay,
            "output": "",
            "error": None,
        }

        # Update accumulated ON time before executing new command
        self._update_on_time(actual_time)

        # Smart EVU cycling based on state transitions (matching mlp_control.sh logic)
        # Cycle EVU when: ALE/ON -> ON, or ON -> ALE
        needs_evu_cycle = False
        if command == "ON" and self.last_command in ("ALE", "ON"):
            needs_evu_cycle = True
        elif command == "ALE" and self.last_command == "ON":
            needs_evu_cycle = True

        if needs_evu_cycle:
            logger.info(f"State transition {self.last_command} -> {command}: performing EVU cycle")
            cycle_result = self._perform_evu_cycle_internal(actual_time)
            if not cycle_result["success"]:
                logger.warning(f"EVU cycle before {command} failed: {cycle_result.get('error')}")

        # Handle AC pump control based on state transitions
        if command in ("ON", "ALE") and self.last_command == "EVU":
            # Going from EVU to ON/ALE: turn on AC pump
            logger.info(f"Transitioning from EVU to {command}: turning on AC pump")
            self._control_ac_pump(turn_on=True)
        elif command == "EVU" and self.last_command in ("ON", "ALE"):
            # Going from ON/ALE to EVU: turn off AC pump (after executing EVU command)
            pass  # Will turn off after I2C write

        if self.dry_run:
            logger.info(f"DRY-RUN: Would execute pump command: {command}")
            result["success"] = True
            result["output"] = f"DRY-RUN: Command {command} logged but not executed"
            # Update state tracking even in dry-run
            self.last_command = command
            self.last_command_time = actual_time
            self._save_state()
            return result

        try:
            logger.info(f"Executing pump command: {command} (delay: {delay}s)")

            # Get I2C values for command
            reg1, reg2 = self.I2C_COMMANDS[command]

            # Execute via I2C
            if not self._write_i2c(reg1, reg2):
                result["error"] = "I2C write failed"
                logger.error(f"Pump command '{command}' failed: I2C write error")
                return result

            result["success"] = True
            result["output"] = f"Pump command {command} executed via I2C"
            logger.info(f"Pump command '{command}' executed successfully")

            # Handle AC pump for EVU transition (turn off after successful I2C write)
            if command == "EVU" and self.last_command in ("ON", "ALE"):
                logger.info("Transitioning to EVU: turning off AC pump")
                self._control_ac_pump(turn_on=False)

            # Update state tracking on success
            self.last_command = command
            self.last_command_time = actual_time
            self._save_state()

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Pump command '{command}' failed with exception: {e}", exc_info=True)

        return result

    def get_status(self) -> Optional[dict]:
        """
        Get current pump status.

        Returns:
            Dict with pump status or None if unavailable
        """
        if self.dry_run:
            return {
                "mode": "DRY-RUN",
                "status": "simulated",
            }

        # TODO: Implement status reading if mlp_control.sh supports it
        logger.debug("Pump status reading not yet implemented")
        return None

    def validate_command(self, command: str) -> bool:
        """
        Validate a command without executing it.

        Args:
            command: Command to validate

        Returns:
            True if command is valid
        """
        return command in self.VALID_COMMANDS


class MultiLoadController:
    """
    Controller for multiple heating loads with different control methods.

    Manages:
    - Geothermal pump (via PumpController)
    - Garage heater (via Shelly relay - TODO)
    - EV charger (via OCPP - TODO)
    """

    def __init__(self, dry_run: bool = False):
        """
        Initialize multi-load controller.

        Args:
            dry_run: If True, log commands but don't execute
        """
        self.dry_run = dry_run
        self.pump_controller = PumpController(dry_run=dry_run)

        logger.info(f"MultiLoadController initialized (dry_run={dry_run})")

    def execute_load_command(
        self, load_id: str, command: str, scheduled_time: int, actual_time: int
    ) -> dict:
        """
        Execute a command for a specific load.

        Args:
            load_id: Load identifier (geothermal_pump, garage_heater, ev_charger)
            command: Command to execute
            scheduled_time: When command was scheduled (epoch)
            actual_time: When command is being executed (epoch)

        Returns:
            Dict with execution results

        Raises:
            ValueError: If load_id is unknown
        """
        logger.info(f"Executing command for {load_id}: {command}")

        if load_id == "geothermal_pump":
            return self.pump_controller.execute_command(command, scheduled_time, actual_time)

        elif load_id == "garage_heater":
            # TODO: Implement Shelly relay control
            logger.warning("Garage heater control not yet implemented")
            return {
                "success": False,
                "command": command,
                "scheduled_time": scheduled_time,
                "actual_time": actual_time,
                "delay_seconds": actual_time - scheduled_time,
                "error": "Garage heater control not implemented",
            }

        elif load_id == "ev_charger":
            # TODO: Implement OCPP control
            logger.warning("EV charger control not yet implemented")
            return {
                "success": False,
                "command": command,
                "scheduled_time": scheduled_time,
                "actual_time": actual_time,
                "delay_seconds": actual_time - scheduled_time,
                "error": "EV charger control not implemented",
            }

        else:
            raise ValueError(f"Unknown load_id: {load_id}")
