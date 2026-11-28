#!/usr/bin/env python
"""Heat pump controller - safe interface to MLP I2C control."""

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

from src.common.config import get_config
from src.common.logger import setup_logger
from src.control import evu_release
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
    - EVU: EVU-OFF mode (pump off)

    The pump commands never switch the house ventilation, the Shelly relay
    that the hardware layer calls the circulation pump: restarting it while
    the wood oven fire starts pulls smoke indoors. See docs/AC_CONTROL_SPEC.md.
    """

    # Valid pump commands
    VALID_COMMANDS = {"ON", "ALE", "EVU"}

    # Maximum delay to execute a command (seconds)
    MAX_EXECUTION_DELAY = 1800  # 30 minutes

    # An EVU-off release blocks the pump's direct heating for 120 min. The
    # executor checks every 15 min, so a pulse is due at the last check inside
    # the window, with 2 min for the 30 s pulse and a late run.
    DIRECT_HEATING_BLOCK = 120 * 60
    EXECUTOR_INTERVAL = 15 * 60
    PULSE_MARGIN = 2 * 60
    PULSE_DUE_AFTER = DIRECT_HEATING_BLOCK - EXECUTOR_INTERVAL - PULSE_MARGIN
    EVU_CYCLE_DURATION = 30  # Cycle EVU-OFF for 30 seconds

    RELAY_ATTEMPTS = 3
    RELAY_RETRY_WAIT = 2

    def __init__(
        self,
        hardware: Optional[PumpHardwareInterface] = None,
        state_file: Optional[str] = None,
        clock: Optional[Callable[[], int]] = None,
        dry_run: bool = False,
        shelly_url: Optional[str] = None,
        source: str = "executor",
    ):
        """Hardware and state file default from the environment; clock is for
        tests. dry_run and shelly_url are deprecated: inject hardware instead.
        source names the caller in the EVU release log."""
        self.source = source
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
        # When EVU was last released, by any caller that shares the state file
        self.last_release_time: Optional[int] = None

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
                    self.last_release_time = state.get("last_release_time")
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
                "last_release_time": self.last_release_time,
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

    def last_release(self) -> Optional[int]:
        """The last EVU release; a state file from before last_release_time
        has only the last pulse. None if unknown."""
        if self.last_release_time is not None:
            return self.last_release_time
        return self.last_evu_cycle_time

    def pulse_due(self, at: int) -> bool:
        """True when the last release is PULSE_DUE_AFTER old at the given
        time, or unknown: the direct heating block would end before the
        executor checks again."""
        released = self.last_release()
        if released is None:
            logger.info("EVU pulse due: no release known")
            return True
        age = at - released
        if age >= self.PULSE_DUE_AFTER:
            logger.info(f"EVU pulse due: last release {age}s ago >= {self.PULSE_DUE_AFTER}s")
            return True
        return False

    def check_evu_cycle_needed(self, current_time: Optional[int] = None) -> bool:
        """True only while the pump is ON and a pulse is due."""
        if current_time is None:
            current_time = self.clock()
        return self.last_command == "ON" and self.pulse_due(current_time)

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

    def _evu_and_wait(self) -> Optional[str]:
        """Write EVU-OFF and hold it for EVU_CYCLE_DURATION (no wait in test
        mode, but in staging). Returns the error, or None."""
        evu_result = self._execute_raw_command("EVU")
        logger.info(f"EVU-OFF command: {evu_result['output']}")
        if not evu_result["success"]:
            return f"Failed to enable EVU: {evu_result['error']}"
        if self.test_mode:
            logger.info(f"TEST MODE: Skipping {self.EVU_CYCLE_DURATION}s wait for EVU cycle")
        else:
            logger.info(f"Waiting {self.EVU_CYCLE_DURATION}s for EVU cycle...")
            time.sleep(self.EVU_CYCLE_DURATION)
        return None

    def _record_evu(self, at: Optional[int]) -> None:
        """The relays were last written EVU, so record that."""
        self.last_command, self.last_command_time, self.on_since = "EVU", at, None
        self._save_state()

    def _perform_evu_cycle_internal(self, current_time: int) -> dict:
        """The EVU half of a pulse before a mode change; the caller writes the
        mode that releases it. Returns success and error."""
        logger.info(f"Performing EVU cycle before a mode change from {self.last_command}")
        error = self._evu_and_wait()
        if error is not None:
            return {"success": False, "error": error}
        # The command that follows sets on_since and the release
        self.last_evu_cycle_time = current_time
        self._save_state()
        logger.info("EVU cycle completed")
        return {"success": True, "error": None}

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
        error = self._evu_and_wait()
        if error is not None:
            result["error"] = error
            logger.error(error)
            return result

        restore_result = self._execute_raw_command("ON")
        logger.info(f"Restore to ON: {restore_result['output']}")
        if not restore_result["success"]:
            self._record_evu(current_time)
            result["error"] = f"Failed to restore ON: {restore_result['error']}"
            logger.error(result["error"])
            return result

        # The pulse is not a command, so the time of the last real command
        # stays for the program and restore checks.
        self.on_since = current_time + self.EVU_CYCLE_DURATION
        self.last_evu_cycle_time = current_time
        self.last_release_time = self.on_since
        self._save_state()
        self._released("ON", "ON", "pulse", "pulse")

        result["success"] = True
        result["restored_command"] = "ON"
        logger.info("EVU cycle completed successfully")
        return result

    def pre_pulse(self, current_time: int) -> dict:
        """Pulse EVU-OFF from ALE or an unknown state and release to ALE, so a
        coming ON finds a fresh release and an idle compressor."""
        result: dict[str, Any] = {"success": False, "error": None}
        if self.last_command not in ("ALE", None):
            result["error"] = f"Pump is {self.last_command}, not ALE: no pre-pulse"
            logger.warning(result["error"])
            return result
        cycle = self._perform_evu_cycle_internal(current_time)
        if not cycle["success"]:
            result["error"] = cycle["error"]
            logger.error(f"Pre-pulse failed: {result['error']}")
            return result
        previous = self.last_command
        if not self._execute_raw_command("ALE")["success"]:
            self._record_evu(current_time)
            result["error"] = "Failed to release to ALE"
            logger.error(result["error"])
            return result
        self.last_command = "ALE"
        self.last_release_time = current_time + self.EVU_CYCLE_DURATION
        self._save_state()
        self._released("ALE", previous, "pre_pulse", "pulse")
        result["success"] = True
        logger.info("Pre-pulse completed: released to ALE")
        return result

    def _released(
        self, new_state: str, previous: Optional[str], via: str, source: Optional[str] = None
    ) -> None:
        """Log an EVU release. Called only after the command and the state save."""
        mock = isinstance(self.hardware, MockHardwareInterface)
        who = source if source is not None else self.source
        evu_release.log_release(who, new_state, str(previous), via, self.clock(), mock)

    def _should_perform_evu_cycle(self, command: str, at: int) -> bool:
        """Pulse into ON from ALE or an unknown state once a pulse is due. ALE
        needs no release, EVU -> ON is one, and a fresh release still runs."""
        if command != "ON" or self.last_command not in ("ALE", None):
            return False
        if self.pulse_due(at):
            return True
        logger.info(f"{self.last_command} -> ON without a pulse: the last release is fresh")
        return False

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
        pulsed = False
        if self._should_perform_evu_cycle(command, actual_time):
            logger.info(f"State transition {self.last_command} -> {command}: performing EVU cycle")
            cycle_result = self._perform_evu_cycle_internal(actual_time)
            pulsed = cycle_result["success"]
            if not cycle_result["success"]:
                logger.warning(f"EVU cycle before {command} failed: {cycle_result['error']}")

        try:
            logger.info(f"Executing pump command: {command} (delay: {result['delay_seconds']}s)")

            # Execute via hardware interface
            success = self._execute_raw_command(command)["success"]

            if not success:
                if pulsed:
                    self._record_evu(actual_time)
                result["error"] = "Hardware command failed"
                logger.error(f"Pump command '{command}' failed: hardware error")
                return result

            result["success"] = True
            result["output"] = f"Pump command {command} executed via hardware interface"
            logger.info(f"Pump command '{command}' executed successfully")
            self._record_command(command, actual_time, pulsed)

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Pump command '{command}' failed with exception: {e}", exc_info=True)

        return result

    def _record_command(self, command: str, actual_time: int, pulsed: bool) -> None:
        """Track a written command. A repeated ON keeps on_since; a failed
        pulse leaves the old release, so the next check retries."""
        if command != "ON":
            self.on_since = None
        elif self.last_command != "ON" or self.on_since is None:
            self.on_since = actual_time
        previous = self.last_command
        self.last_command = command
        self.last_command_time = actual_time
        released = command != "EVU" and (pulsed or previous == "EVU")
        if released:
            self.last_release_time = actual_time + (self.EVU_CYCLE_DURATION if pulsed else 0)
        self._save_state()
        if released:
            self._released(command, previous, "ale_on_pulse" if pulsed else "command")

    def apply_state(self, command: str, current_time: int, clock_ok: bool = True) -> dict:
        """Set the pump to command from any unknown state, as after a reboot.
        ON and ALE get an EVU-off pulse first. An unsynced clock is not
        recorded as the command time."""
        if command not in self.VALID_COMMANDS:
            raise ValueError(f"Invalid command '{command}'")

        stamp = current_time if clock_ok else self.last_command_time
        result: dict[str, Any] = {"success": False, "command": command, "error": None}
        if command in ("ON", "ALE"):
            error = self._evu_and_wait()
            if error is not None:
                result["error"] = error
                return result
        applied = self._execute_raw_command(command)
        if not applied["success"]:
            if command in ("ON", "ALE"):
                self._record_evu(stamp)
            result["error"] = f"{command} failed: {applied['error']}"
            return result

        released = current_time + self.EVU_CYCLE_DURATION
        previous = self.last_command
        self.last_command = command
        self.last_command_time = stamp
        if command in ("ON", "ALE"):
            self.last_evu_cycle_time = current_time
            self.last_release_time = released
        self.on_since = released if command == "ON" else None
        self._save_state()
        if command in ("ON", "ALE"):
            self._released(command, previous, "boot_pulse", "boot")
        result["success"] = True
        logger.info(f"Pump state {command} applied from an unknown state")
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
