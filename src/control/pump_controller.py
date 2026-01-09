#!/usr/bin/env python
"""Heat pump controller - safe interface to MLP I2C control."""

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

from src.common.logger import setup_logger
from src.control.hardware_implementations import (
    CombinedHardwareInterface,
    MockHardwareInterface,
)
from src.control.hardware_interface import PumpHardwareInterface

logger = setup_logger(__name__)


class PumpController:
    """
    Safe interface to geothermal heat pump control via I2C.

    Business logic for pump control with hardware access via dependency injection.
    Uses hardware interface for all physical device interactions.

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

    def __init__(
        self,
        hardware: Optional[PumpHardwareInterface] = None,
        state_file: Optional[str] = None,
        clock: Optional[Callable[[], int]] = None,
        dry_run: bool = False,
        shelly_url: Optional[str] = None,
    ):
        """
        Initialize pump controller with dependency injection.

        Args:
            hardware: Hardware interface implementation (defaults to auto-detect)
            state_file: Path to state file (default: data/pump_state.json)
            clock: Time source for testing (defaults to time.time)
            dry_run: DEPRECATED - use hardware=MockHardwareInterface() instead
            shelly_url: DEPRECATED - configure hardware interface directly
        """
        # Auto-detect hardware if not provided (backward compatibility)
        if hardware is None:
            hardware = self._create_default_hardware(dry_run, shelly_url)

        self.hardware = hardware
        self.state_file = Path(state_file or "data/pump_state.json")
        self.clock = clock or (lambda: int(time.time()))

        # Check if using mock hardware (for test mode detection)
        self.test_mode = isinstance(hardware, MockHardwareInterface) and os.getenv(
            "TEST_MODE", "false"
        ).lower() in ("true", "1", "yes")

        # EVU cycle state tracking (pure business logic)
        self.on_time_accumulated = 0  # Total ON time since last EVU cycle (seconds)
        self.last_command: Optional[str] = None
        self.last_command_time: Optional[int] = None
        self.last_evu_cycle_time: Optional[int] = None

        # Load saved state
        self._load_state()

        # Log initialization mode
        logger.info(f"PumpController initialized with {type(hardware).__name__}")

    def _create_default_hardware(
        self, dry_run: bool = False, shelly_url: Optional[str] = None
    ) -> PumpHardwareInterface:
        """
        Create default hardware interface based on environment.

        Args:
            dry_run: If True, use mock hardware
            shelly_url: Optional Shelly relay URL (deprecated)

        Returns:
            Hardware interface implementation
        """
        # Check for test/staging mode
        staging_mode = os.getenv("STAGING_MODE", "false").lower() in ("true", "1", "yes")
        test_mode = os.getenv("TEST_MODE", "false").lower() in ("true", "1", "yes")

        if dry_run or test_mode or staging_mode:
            logger.info("Using mock hardware interface (test/staging/dry-run mode)")
            return MockHardwareInterface()

        # Try real hardware
        try:
            logger.info("Using combined I2C + Shelly hardware interface")
            return CombinedHardwareInterface(relay_url=shelly_url)
        except Exception as e:
            logger.warning(f"Hardware initialization failed, using mock: {e}")
            return MockHardwareInterface()

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

    def check_evu_cycle_needed(self, current_time: Optional[int] = None) -> bool:
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
            current_time = self.clock()

        # Update accumulated ON time
        self._update_on_time(current_time)

        if self.on_time_accumulated >= self.EVU_CYCLE_THRESHOLD:
            logger.info(
                f"EVU cycle needed: ON time {self.on_time_accumulated}s "
                f">= threshold {self.EVU_CYCLE_THRESHOLD}s"
            )
            return True

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

        if command not in self.VALID_COMMANDS:
            result["error"] = f"Unknown command: {command}"
            return result

        # Execute via hardware interface
        success = self.hardware.write_pump_command(command)

        if not success:
            result["error"] = "Hardware command failed"
            return result

        result["success"] = True
        result["output"] = f"{command} executed successfully"
        logger.info(f"Pump command {command} executed via hardware interface")

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

        result: dict[str, Any] = {"success": False, "on_time_before": self.on_time_accumulated}

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

    def perform_evu_cycle(self, current_time: Optional[int] = None) -> dict:
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
            current_time = self.clock()

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
            self.hardware.control_circulation_pump(turn_on=True)
        elif command == "EVU" and self.last_command in ("ON", "ALE"):
            # Going from ON/ALE to EVU: turn off AC pump (after executing EVU command)
            pass  # Will turn off after hardware write

        try:
            logger.info(f"Executing pump command: {command} (delay: {delay}s)")

            # Execute via hardware interface
            success = self.hardware.write_pump_command(command)

            if not success:
                result["error"] = "Hardware command failed"
                logger.error(f"Pump command '{command}' failed: hardware error")
                return result

            result["success"] = True
            result["output"] = f"Pump command {command} executed via hardware interface"
            logger.info(f"Pump command '{command}' executed successfully")

            # Handle AC pump for EVU transition (turn off after successful write)
            if command == "EVU" and self.last_command in ("ON", "ALE"):
                logger.info("Transitioning to EVU: turning off AC pump")
                self.hardware.control_circulation_pump(turn_on=False)

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
        return self.hardware.get_pump_status()

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
