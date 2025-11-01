#!/usr/bin/env python
"""Heat pump controller - safe interface to MLP I2C control."""

import subprocess
from typing import Optional

from src.common.logger import setup_logger

logger = setup_logger(__name__)


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

    def __init__(self, script_path: str = "./mlp_control.sh", dry_run: bool = False):
        """
        Initialize pump controller.

        Args:
            script_path: Path to mlp_control.sh script
            dry_run: If True, log commands but don't execute
        """
        self.script_path = script_path
        self.dry_run = dry_run

        if dry_run:
            logger.info("PumpController initialized in DRY-RUN mode")
        else:
            logger.info(f"PumpController initialized with script: {script_path}")

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

        if self.dry_run:
            logger.info(f"DRY-RUN: Would execute: {self.script_path} {command}")
            result["success"] = True
            result["output"] = f"DRY-RUN: Command {command} logged but not executed"
            return result

        try:
            logger.info(f"Executing pump command: {command} (delay: {delay}s)")

            # Execute via subprocess
            process = subprocess.run(
                [self.script_path, command],
                capture_output=True,
                text=True,
                timeout=30,  # 30 second timeout
            )

            result["output"] = process.stdout.strip()
            result["success"] = process.returncode == 0

            if process.returncode != 0:
                result["error"] = process.stderr.strip()
                logger.error(
                    f"Pump command '{command}' failed: {process.returncode} - {result['error']}"
                )
            else:
                logger.info(f"Pump command '{command}' executed successfully")

        except subprocess.TimeoutExpired:
            result["error"] = "Command timed out after 30 seconds"
            logger.error(f"Pump command '{command}' timed out")

        except FileNotFoundError:
            result["error"] = f"Script not found: {self.script_path}"
            logger.error(f"Pump control script not found: {self.script_path}")

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
