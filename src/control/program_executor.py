#!/usr/bin/env python
"""Execute daily heating programs safely."""

import datetime
import json
import os
import time
from typing import Optional

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger
from src.control.pump_controller import MultiLoadController

logger = setup_logger(__name__)


class HeatingProgramExecutor:
    """
    Execute daily heating programs with safe load control.

    Features:
    - Load v2.0 JSON programs
    - Execute commands at scheduled times
    - Multi-load support (pump, garage, EV)
    - Mark commands as executed
    - Write actual execution to InfluxDB
    - Handle day transitions
    - Dry-run mode for testing
    """

    VERSION = "2.0.0"

    # Maximum delay to execute commands (seconds)
    MAX_EXECUTION_DELAY = 1800  # 30 minutes

    def __init__(self, config: Optional[dict] = None, dry_run: bool = False):
        """
        Initialize program executor.

        Args:
            config: Configuration dict (uses get_config() if None)
            dry_run: If True, log commands but don't execute
        """
        self.config = config or get_config()
        self.dry_run = dry_run
        self.influx = InfluxClient(self.config)
        self.load_controller = MultiLoadController(dry_run=dry_run)

        logger.info(f"Initialized HeatingProgramExecutor v{self.VERSION} (dry_run={dry_run})")

    def load_program(self, program_date: Optional[str] = None, base_dir: str = ".") -> dict:
        """
        Load heating program JSON for a specific date.

        Args:
            program_date: Date to load (YYYY-MM-DD), defaults to today
            base_dir: Base directory for program files

        Returns:
            Program dict

        Raises:
            FileNotFoundError: If program file doesn't exist
            ValueError: If program file is invalid
        """
        if program_date is None:
            program_date = datetime.date.today().strftime("%Y-%m-%d")

        year_month = program_date[:7]  # YYYY-MM
        filename = f"heating_program_schedule_{program_date}.json"
        filepath = os.path.join(base_dir, year_month, filename)

        logger.info(f"Loading program from: {filepath}")

        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Program file not found: {filepath}")

        with open(filepath) as f:
            program = json.load(f)

        # Validate program structure
        if "version" not in program:
            raise ValueError("Invalid program: missing 'version' field")

        if "loads" not in program:
            raise ValueError("Invalid program: missing 'loads' field")

        logger.info(f"Loaded program v{program.get('version')} for {program.get('program_date')}")

        return program

    def save_program(self, program: dict, base_dir: str = "."):
        """
        Save updated program with execution status.

        Args:
            program: Program dict to save
            base_dir: Base directory for program files
        """
        program_date = program["program_date"]
        year_month = program_date[:7]
        filename = f"heating_program_schedule_{program_date}.json"
        filepath = os.path.join(base_dir, year_month, filename)

        with open(filepath, "w") as f:
            json.dump(program, f, indent=2)

        logger.debug(f"Saved updated program to: {filepath}")

    def execute_program(
        self, program: dict, current_time: Optional[int] = None, base_dir: str = "."
    ) -> dict:
        """
        Execute all pending commands in the program.

        Args:
            program: Program dict from load_program()
            current_time: Current time (epoch), defaults to now
            base_dir: Base directory for saving updated program

        Returns:
            Execution summary dict with:
            - executed_count: Number of commands executed
            - skipped_count: Number of commands skipped
            - failed_count: Number of commands that failed
            - next_execution_time: Next scheduled command time
        """
        if current_time is None:
            current_time = int(time.time())

        logger.info(f"Executing program at {datetime.datetime.fromtimestamp(current_time)}")

        # Collect all commands from all loads
        all_commands = []
        for load_id, load_data in program["loads"].items():
            for entry in load_data["schedule"]:
                all_commands.append(
                    {
                        "load_id": load_id,
                        "entry": entry,
                        "timestamp": entry["timestamp"],
                    }
                )

        # Sort by timestamp
        all_commands.sort(key=lambda x: x["timestamp"])

        executed_count = 0
        skipped_count = 0
        failed_count = 0
        next_execution_time = None

        # Execute pending commands
        for cmd_info in all_commands:
            entry = cmd_info["entry"]
            scheduled_time = entry["timestamp"]

            # Skip if already executed
            if entry.get("executed_at"):
                continue

            # Check if time to execute
            if current_time >= scheduled_time:
                # Check delay
                delay = current_time - scheduled_time
                if delay > self.MAX_EXECUTION_DELAY:
                    logger.warning(
                        f"Skipping command '{entry['command']}' at {entry['local_time']}: "
                        f"delay too large ({delay}s > {self.MAX_EXECUTION_DELAY}s)"
                    )
                    skipped_count += 1
                    continue

                # Execute command
                result = self._execute_command(
                    cmd_info["load_id"], entry, scheduled_time, current_time
                )

                if result["success"]:
                    executed_count += 1
                    entry["executed_at"] = current_time
                    entry["execution_result"] = result

                    # Save program after each execution
                    self.save_program(program, base_dir)

                    # Write to InfluxDB
                    self._write_execution_to_influx(
                        program["program_date"], cmd_info["load_id"], entry, result
                    )
                else:
                    failed_count += 1
                    logger.error(
                        f"Failed to execute command '{entry['command']}' "
                        f"for {cmd_info['load_id']}: {result.get('error')}"
                    )

            else:
                # This is a future command
                if next_execution_time is None:
                    next_execution_time = scheduled_time
                break

        # Check if periodic EVU cycle is needed (for geothermal pump)
        evu_cycle_performed = False
        if hasattr(self.load_controller, "pump_controller"):
            pump_ctrl = self.load_controller.pump_controller
            if pump_ctrl.check_evu_cycle_needed(current_time):
                logger.info("Periodic EVU cycle needed (105 min threshold reached)")
                cycle_result = pump_ctrl.perform_evu_cycle(current_time)
                if cycle_result["success"]:
                    logger.info("Periodic EVU cycle completed successfully")
                    evu_cycle_performed = True
                else:
                    logger.error(f"Periodic EVU cycle failed: {cycle_result.get('error')}")

        # Update execution status in program
        total_commands = len([c for c in all_commands if not c["entry"].get("executed_at")])
        program["execution_status"] = {
            "executed_intervals": executed_count,
            "pending_intervals": total_commands - executed_count,
            "last_executed_timestamp": current_time if executed_count > 0 else None,
            "next_execution_timestamp": next_execution_time,
            "evu_cycle_performed": evu_cycle_performed,
        }

        summary = {
            "executed_count": executed_count,
            "skipped_count": skipped_count,
            "failed_count": failed_count,
            "next_execution_time": next_execution_time,
            "evu_cycle_performed": evu_cycle_performed,
        }

        logger.info(
            f"Execution complete: {executed_count} executed, {skipped_count} skipped, "
            f"{failed_count} failed, EVU cycle: {evu_cycle_performed}"
        )

        return summary

    def _execute_command(
        self, load_id: str, entry: dict, scheduled_time: int, actual_time: int
    ) -> dict:
        """
        Execute a single command.

        Args:
            load_id: Load identifier
            entry: Schedule entry dict
            scheduled_time: When command was scheduled
            actual_time: Current time

        Returns:
            Execution result dict
        """
        command = entry["command"]

        logger.info(
            f"Executing {load_id}: {command} at {entry['local_time']} "
            f"(delay: {actual_time - scheduled_time}s)"
        )

        try:
            result = self.load_controller.execute_load_command(
                load_id, command, scheduled_time, actual_time
            )

            if result["success"]:
                logger.info(f"Successfully executed {load_id}: {command}")
            else:
                logger.error(f"Failed to execute {load_id}: {command} - {result.get('error')}")

            return result

        except Exception as e:
            logger.error(f"Exception executing {load_id}: {command} - {e}", exc_info=True)
            return {
                "success": False,
                "command": command,
                "scheduled_time": scheduled_time,
                "actual_time": actual_time,
                "delay_seconds": actual_time - scheduled_time,
                "error": str(e),
            }

    def _write_execution_to_influx(
        self, program_date: str, load_id: str, entry: dict, result: dict
    ):
        """
        Write actual execution to InfluxDB.

        Args:
            program_date: Program date (YYYY-MM-DD)
            load_id: Load identifier
            entry: Schedule entry dict
            result: Execution result dict
        """
        if self.dry_run:
            logger.debug("DRY-RUN: Skipping InfluxDB write")
            return

        try:
            from influxdb_client import Point

            timestamp = datetime.datetime.fromtimestamp(result["actual_time"])
            command = entry["command"]

            # Determine if load is ON
            is_on = command == "ON"
            is_evu_off = command == "EVU"
            power_kw = entry.get("power_kw", 0.0) if is_on else 0.0

            point = (
                Point("load_control")
                .tag("program_date", program_date)
                .tag("load_id", load_id)
                .tag("data_type", "actual")
                .field("command", command)
                .field("power_kw", power_kw)
                .field("is_on", is_on)
                .field("is_evu_off", is_evu_off)
                .field("scheduled_time", result["scheduled_time"])
                .field("actual_time", result["actual_time"])
                .field("delay_seconds", result["delay_seconds"])
                .field("success", result["success"])
                .field("reason", entry.get("reason", "unknown"))
                .time(timestamp)
            )

            bucket_name = self.config.get("influxdb_bucket_load_control", "load_control")
            self.influx.write_api.write(bucket=bucket_name, record=point)

            logger.debug(f"Wrote execution to InfluxDB: {load_id} {command}")

        except Exception as e:
            logger.error(f"Failed to write execution to InfluxDB: {e}")

    def handle_day_transition(
        self, today_program: dict, yesterday_program: Optional[dict] = None
    ) -> dict:
        """
        Handle day transition - merge yesterday's unexecuted commands.

        Args:
            today_program: Today's program
            yesterday_program: Yesterday's program (optional)

        Returns:
            Updated today's program with merged commands
        """
        if yesterday_program is None:
            logger.debug("No yesterday program provided, skipping day transition")
            return today_program

        logger.info("Handling day transition - checking for unexecuted commands from yesterday")

        merged_count = 0

        # Check each load in yesterday's program
        for load_id, yesterday_load in yesterday_program.get("loads", {}).items():
            if load_id not in today_program["loads"]:
                logger.warning(f"Load {load_id} in yesterday but not in today's program")
                continue

            today_load = today_program["loads"][load_id]
            today_timestamps = {e["timestamp"] for e in today_load["schedule"]}

            # Find unexecuted commands from yesterday
            for entry in yesterday_load["schedule"]:
                if not entry.get("executed_at") and entry["timestamp"] not in today_timestamps:
                    logger.info(
                        f"Merging unexecuted command from yesterday: "
                        f"{load_id} {entry['command']} at {entry['local_time']}"
                    )
                    today_load["schedule"].append(entry)
                    merged_count += 1

            # Re-sort schedule
            today_load["schedule"].sort(key=lambda x: x["timestamp"])

        if merged_count > 0:
            logger.info(f"Merged {merged_count} unexecuted commands from yesterday")
        else:
            logger.info("No unexecuted commands to merge from yesterday")

        return today_program
