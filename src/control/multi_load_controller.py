#!/usr/bin/env python
"""Multi-load controller for managing multiple heating loads."""

from src.common.logger import setup_logger
from src.control.pump_controller import PumpController

logger = setup_logger(__name__)


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
