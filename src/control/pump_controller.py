#!/usr/bin/env python
"""Heat pump controller - safe interface to MLP I2C control."""

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

from src.common.config import get_config
from src.common.logger import setup_logger
from src.control.hardware_implementations import (
    CombinedHardwareInterface,
    MockHardwareInterface,
)
from src.control.hardware_interface import PumpHardwareInterface

logger = setup_logger(__name__)

# Fixed to the install, so a manual control_pump.py run from any directory
# shares its state with the executor and the boot restore
DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class PumpController:
    """
    Safe interface to geothermal heat pump control via I2C.

    Business logic for pump control with hardware access via dependency injection.
    Uses hardware interface for all physical device interactions.

    Commands:
    - ON: Normal heating mode
    - ALE: Lower temperature mode
    - EVU: EVU-OFF mode (pump off, circulation pump off)
    """

    # Valid pump commands
    VALID_COMMANDS = {"ON", "ALE", "EVU"}

    # Maximum delay to execute a command (seconds)
    MAX_EXECUTION_DELAY = 1800  # 30 minutes

    # An EVU-off release blocks the pump's direct heating for 120 min. The
    # executor checks every 15 min, so pulse at the last check that still
    # falls inside the window, with 2 min to spare: every 105 min in ON.
    DIRECT_HEATING_BLOCK = 120 * 60
    EXECUTOR_INTERVAL = 15 * 60
    PULSE_MARGIN = 2 * 60
    EVU_CYCLE_THRESHOLD = DIRECT_HEATING_BLOCK - EXECUTOR_INTERVAL - PULSE_MARGIN
    EVU_CYCLE_DURATION = 30  # Cycle EVU-OFF for 30 seconds

    # After a power cut the Shelly can boot later than the Pi
    SHELLY_ATTEMPTS = 7
    SHELLY_RETRY_WAIT = 10
    RELAY_ATTEMPTS = 3
    RELAY_RETRY_WAIT = 2

    def __init__(
        self,
        hardware: Optional[PumpHardwareInterface] = None,
        state_file: Optional[str] = None,
        clock: Optional[Callable[[], int]] = None,
        dry_run: bool = False,
        shelly_url: Optional[str] = None,
    ):
        """Hardware and state file default from the environment; clock is for
        tests. dry_run and shelly_url are deprecated: inject hardware instead."""
        # Auto-detect hardware if not provided (backward compatibility)
        if hardware is None:
            hardware = self._create_default_hardware(dry_run, shelly_url)

        self.hardware = hardware
        # A dry run must never overwrite the state the real pump runs on
        if state_file is None:
            mock = isinstance(hardware, MockHardwareInterface)
            state_file = str(DATA_DIR / ("pump_state_dry_run.json" if mock else "pump_state.json"))
        self.state_file = Path(state_file)
        self.clock = clock or (lambda: int(time.time()))

        # Check if using mock hardware (for test mode detection)
        self.test_mode = isinstance(hardware, MockHardwareInterface) and os.getenv(
            "TEST_MODE", "false"
        ).lower() in ("true", "1", "yes")

        # When the pump entered ON or was last EVU-cycled while ON; None otherwise
        self.on_since: Optional[int] = None
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
        """Create default hardware interface based on environment."""
        # Check for test/staging mode
        staging_mode = os.getenv("STAGING_MODE", "false").lower() in ("true", "1", "yes")
        test_mode = os.getenv("TEST_MODE", "false").lower() in ("true", "1", "yes")

        if dry_run or test_mode or staging_mode:
            logger.info("Using mock hardware interface (test/staging/dry-run mode)")
            return MockHardwareInterface()

        # Real hardware. A failure here must stop the run: silently using the
        # mock would report pump commands as done while nothing moved.
        config = get_config()
        relay = shelly_url or (config.shelly_relay_url + "/relay/0")
        logger.info("Using combined I2C + Shelly hardware interface")
        return CombinedHardwareInterface(
            i2c_bus=config.pump_i2c_bus,
            i2c_address=config.pump_i2c_address,
            relay_url=relay,
        )

    def _load_state(self):
        """Load EVU cycle state from file."""
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    state = json.load(f)
                    self.last_command = state.get("last_command")
                    self.last_command_time = state.get("last_command_time")
                    self.last_evu_cycle_time = state.get("last_evu_cycle_time")
                    self.on_since = state.get("on_since")
                    if self.on_since is None and self.last_command == "ON":
                        # A state file from before on_since: the later of the two
                        known = [t for t in (self.last_command_time, self.last_evu_cycle_time) if t]
                        self.on_since = max(known) if known else None
                    logger.info(
                        f"Loaded pump state: last_command={self.last_command}, "
                        f"on_since={self.on_since}"
                    )
            except Exception as e:
                logger.warning(f"Failed to load pump state: {e}")

    def _save_state(self):
        """Save EVU cycle state to file."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "on_since": self.on_since,
                "last_command": self.last_command,
                "last_command_time": self.last_command_time,
                "last_evu_cycle_time": self.last_evu_cycle_time,
            }
            # A power cut mid-write must not leave a truncated state file
            partial = self.state_file.with_name(self.state_file.name + ".tmp")
            with open(partial, "w") as f:
                json.dump(state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(partial, self.state_file)
        except Exception as e:
            logger.error(f"Failed to save pump state: {e}")

    def check_evu_cycle_needed(self, current_time: Optional[int] = None) -> bool:
        """
        True only while the pump is ON and has been ON, without an EVU-off
        pulse, for at least EVU_CYCLE_THRESHOLD seconds.
        """
        if current_time is None:
            current_time = self.clock()

        if self.last_command != "ON" or self.on_since is None:
            return False

        on_for = current_time - self.on_since
        if on_for >= self.EVU_CYCLE_THRESHOLD:
            logger.info(f"EVU cycle needed: ON for {on_for}s >= {self.EVU_CYCLE_THRESHOLD}s")
            return True
        return False

    def _execute_raw_command(self, command: str) -> dict:
        """Write a relay command without state tracking, retrying a few times:
        a pulse that fails at the last check before the window ends cannot
        wait for the next run. Returns success, output and error."""
        result = {"success": False, "output": "", "error": None}

        if command not in self.VALID_COMMANDS:
            result["error"] = f"Unknown command: {command}"
            return result

        success = False
        for attempt in range(1, self.RELAY_ATTEMPTS + 1):
            success = self.hardware.write_pump_command(command)
            if success:
                break
            logger.warning(f"Relay write {command} attempt {attempt} failed")
            if attempt < self.RELAY_ATTEMPTS and not self.test_mode:
                time.sleep(self.RELAY_RETRY_WAIT)

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
        logger.info(f"Performing EVU cycle before a mode change from {self.last_command}")

        result: dict[str, Any] = {"success": False}

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

        # Step 3: Record the pulse; the command that follows sets on_since
        self.last_evu_cycle_time = current_time
        self._save_state()

        result["success"] = True
        logger.info("EVU cycle completed")
        return result

    def perform_evu_cycle(self, current_time: Optional[int] = None) -> dict:
        """
        Pulse EVU-OFF for 30 seconds while the pump is ON, then return to ON.
        Refuses in any other state: a pulse there could only end in ON.
        """
        if current_time is None:
            current_time = self.clock()

        result = {
            "success": False,
            "cycle_time": current_time,
            "previous_command": self.last_command,
        }
        if self.last_command != "ON":
            result["error"] = f"Pump is {self.last_command}, not ON: no EVU cycle"
            logger.warning(result["error"])
            return result

        logger.info(f"Performing EVU cycle: ON since {self.on_since}")

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

        # Step 3: Return to ON
        restore_result = self._execute_raw_command("ON")
        logger.info(f"Restore to ON: {restore_result['output']}")

        if not restore_result["success"]:
            # The relays were last written EVU, so record that, not ON
            self.last_command = "EVU"
            self.last_command_time = current_time
            self.on_since = None
            self._save_state()
            result["error"] = f"Failed to restore ON: {restore_result.get('error')}"
            logger.error(result["error"])
            return result

        # Step 4: Update state. The pulse is not a command, so the time of
        # the last real command stays for the program and restore checks.
        self.on_since = current_time + self.EVU_CYCLE_DURATION
        self.last_evu_cycle_time = current_time
        self._save_state()

        result["success"] = True
        result["restored_command"] = "ON"
        logger.info("EVU cycle completed successfully")

        return result

    def _should_perform_evu_cycle(self, command: str) -> bool:
        """Pulse into ON from ALE or an unknown state, where the pump is likely
        behind its heating curve. ALE needs no release, and EVU -> ON is one."""
        return command == "ON" and self.last_command in ("ALE", None)

    def _create_result_dict(self, command: str, scheduled_time: int, actual_time: int) -> dict:
        """A failed result for the command, with its timing metadata."""
        delay = actual_time - scheduled_time

        if delay > self.MAX_EXECUTION_DELAY:
            logger.warning(
                f"Command '{command}' delayed by {delay}s (max: {self.MAX_EXECUTION_DELAY}s)"
            )

        return {
            "success": False,
            "command": command,
            "scheduled_time": scheduled_time,
            "actual_time": actual_time,
            "delay_seconds": delay,
            "output": "",
            "error": None,
        }

    def _handle_circulation_pump_on_transition(self, command: str):
        """Before the command: EVU -> ON or ALE turns the AC pump on."""
        if command in ("ON", "ALE") and self.last_command == "EVU":
            logger.info(f"Transitioning from EVU to {command}: turning on AC pump")
            self.hardware.control_circulation_pump(turn_on=True)

    def _handle_circulation_pump_off_transition(self, command: str):
        """After the command: ON or ALE -> EVU turns the AC pump off."""
        if command == "EVU" and self.last_command in ("ON", "ALE"):
            logger.info("Transitioning to EVU: turning off AC pump")
            self.hardware.control_circulation_pump(turn_on=False)

    def execute_command(self, command: str, scheduled_time: int, actual_time: int) -> dict:
        """Execute ON, ALE or EVU (epoch times); raises ValueError otherwise.
        Returns success, command, scheduled_time, actual_time, delay_seconds,
        output and error."""
        # Validate command
        if command not in self.VALID_COMMANDS:
            raise ValueError(
                f"Invalid command '{command}'. Must be one of: {', '.join(self.VALID_COMMANDS)}"
            )

        # Create result dictionary
        result = self._create_result_dict(command, scheduled_time, actual_time)

        # Perform EVU cycle if needed based on state transition
        pulse_failed = False
        pulsed = False
        if self._should_perform_evu_cycle(command):
            logger.info(f"State transition {self.last_command} -> {command}: performing EVU cycle")
            cycle_result = self._perform_evu_cycle_internal(actual_time)
            pulsed = cycle_result["success"]
            if not cycle_result["success"]:
                pulse_failed = True
                logger.warning(f"EVU cycle before {command} failed: {cycle_result.get('error')}")

        # Handle AC pump before command execution
        self._handle_circulation_pump_on_transition(command)

        try:
            logger.info(f"Executing pump command: {command} (delay: {result['delay_seconds']}s)")

            # Execute via hardware interface
            success = self._execute_raw_command(command)["success"]

            if not success:
                if pulsed:
                    # The pulse left the relays in EVU, so record that
                    self.last_command, self.last_command_time, self.on_since = (
                        "EVU",
                        actual_time,
                        None,
                    )
                    self._save_state()
                result["error"] = "Hardware command failed"
                logger.error(f"Pump command '{command}' failed: hardware error")
                return result

            result["success"] = True
            result["output"] = f"Pump command {command} executed via hardware interface"
            logger.info(f"Pump command '{command}' executed successfully")

            # Handle AC pump after command execution
            self._handle_circulation_pump_off_transition(command)

            # Update state tracking on success. A repeated ON keeps on_since;
            # after a failed pulse the window counts as spent, so it retries.
            if command != "ON":
                self.on_since = None
            elif pulse_failed:
                self.on_since = actual_time - self.EVU_CYCLE_THRESHOLD
            elif self.last_command != "ON" or self.on_since is None:
                self.on_since = actual_time
            self.last_command = command
            self.last_command_time = actual_time
            self._save_state()

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Pump command '{command}' failed with exception: {e}", exc_info=True)

        return result

    def apply_state(self, command: str, current_time: int) -> dict:
        """Set the pump to command from any unknown state, as after a reboot.
        ON and ALE get an EVU-off pulse first, which also opens the pump's
        direct-heating block window; the circulation pump is set explicitly."""
        if command not in self.VALID_COMMANDS:
            raise ValueError(f"Invalid command '{command}'")

        result: dict[str, Any] = {"success": False, "command": command, "error": None}
        circulation_ok = True
        if command in ("ON", "ALE"):
            pulse = self._execute_raw_command("EVU")
            if not pulse["success"]:
                result["error"] = f"EVU pulse failed: {pulse['error']}"
                return result
            if not self.test_mode:
                time.sleep(self.EVU_CYCLE_DURATION)
            circulation_ok = self._set_circulation_with_retry(turn_on=True)
        applied = self._execute_raw_command(command)
        if not applied["success"]:
            if command in ("ON", "ALE"):
                # The pulse left the relays in EVU
                self.last_command, self.last_command_time, self.on_since = "EVU", current_time, None
                self._save_state()
            result["error"] = f"{command} failed: {applied['error']}"
            return result
        if command == "EVU":
            circulation_ok = self._set_circulation_with_retry(turn_on=False)

        released = current_time + self.EVU_CYCLE_DURATION
        self.last_command = command
        self.last_command_time = current_time
        if command in ("ON", "ALE"):
            self.last_evu_cycle_time = current_time
        self.on_since = released if command == "ON" else None
        self._save_state()
        if not circulation_ok:
            result["error"] = f"{command} applied, but the circulation pump did not answer"
            logger.error(result["error"])
            return result
        result["success"] = True
        logger.info(f"Pump state {command} applied from an unknown state")
        return result

    def _set_circulation_with_retry(self, turn_on: bool) -> bool:
        for attempt in range(1, self.SHELLY_ATTEMPTS + 1):
            if self.hardware.control_circulation_pump(turn_on=turn_on):
                return True
            logger.warning(f"Circulation pump attempt {attempt} of {self.SHELLY_ATTEMPTS} failed")
            if attempt < self.SHELLY_ATTEMPTS and not self.test_mode:
                time.sleep(self.SHELLY_RETRY_WAIT)
        return False

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
