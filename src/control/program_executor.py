#!/usr/bin/env python
"""Execute daily heating programs safely."""

import datetime
import json
import os
import time
from typing import Optional

from influxdb_client import Point

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger
from src.control.multi_load_controller import MultiLoadController
from src.control.program_shape import PUMP_LOAD, program_is_well_formed

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
            dry_run: If True, log commands but don't execute (also enabled by STAGING_MODE env var)
        """
        self.config = config or get_config()

        # Check staging mode from environment
        staging_mode = os.getenv("STAGING_MODE", "false").lower() in ("true", "1", "yes")
        self.dry_run = dry_run or staging_mode

        self.influx = InfluxClient(self.config)
        self.load_controller = MultiLoadController(dry_run=self.dry_run)

        mode_str = "STAGING" if staging_mode else ("DRY-RUN" if dry_run else "PRODUCTION")
        logger.info(f"Initialized HeatingProgramExecutor v{self.VERSION} ({mode_str} mode)")

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

        # A power cut mid-write must not leave a truncated program
        with open(filepath + ".tmp", "w") as f:
            json.dump(program, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(filepath + ".tmp", filepath)

        logger.debug(f"Saved updated program to: {filepath}")

    def execute_program(
        self,
        program: dict,
        current_time: Optional[int] = None,
        base_dir: str = ".",
        previous_program: Optional[dict] = None,
    ) -> dict:
        """Execute every due command of the program, with the periodic EVU check
        before them (slow InfluxDB writes must not delay it) and after them.
        Returns executed_count, skipped_count, failed_count, next_execution_time."""
        if current_time is None:
            current_time = int(time.time())

        logger.info(f"Executing program at {datetime.datetime.fromtimestamp(current_time)}")

        all_commands: list = []
        executed_count = 0
        skipped_count = 0
        failed_count = 0
        next_execution_time = None

        # Execute pending commands. The pulse check runs even if one raises.
        pulsed_before = False
        pulsed_after = False
        started = time.monotonic()
        try:
            pulsed_before = self.run_periodic_pulse(current_time, program, previous_program)
            all_commands = self._collect_and_sort_commands(program)
            for cmd_info in all_commands:
                entry = cmd_info["entry"]
                scheduled_time = entry["timestamp"]

                # Skip if already executed
                if entry.get("executed_at"):
                    continue

                # Check if time to execute
                if current_time >= scheduled_time:
                    exec_result = self._process_and_execute_command(
                        program, cmd_info, current_time, base_dir
                    )
                    if exec_result == "executed":
                        executed_count += 1
                    elif exec_result == "skipped":
                        skipped_count += 1
                    elif exec_result == "failed":
                        failed_count += 1
                else:
                    # This is a future command
                    if next_execution_time is None:
                        next_execution_time = scheduled_time
                    break
        finally:
            # Slow InfluxDB writes can make the run end much later than it began
            ended = current_time + int(time.monotonic() - started)
            pulsed_after = self.run_periodic_pulse(ended, program, previous_program)
        evu_cycle_performed = pulsed_before or pulsed_after

        summary = self._update_program_summary(
            program,
            all_commands,
            executed_count,
            skipped_count,
            failed_count,
            next_execution_time,
            evu_cycle_performed,
            current_time,
        )

        logger.info(
            f"Execution complete: {executed_count} executed, {skipped_count} skipped, "
            f"{failed_count} failed, EVU cycle: {evu_cycle_performed}"
        )

        return summary

    def _process_and_execute_command(
        self, program: dict, cmd_info: dict, current_time: int, base_dir: str
    ) -> str:
        """Execute one command (cmd_info has load_id, entry, timestamp) unless it
        is too late. Returns "executed", "skipped" or "failed"."""
        entry = cmd_info["entry"]
        scheduled_time = entry["timestamp"]

        # Check delay
        delay = current_time - scheduled_time
        if delay > self.MAX_EXECUTION_DELAY:
            logger.warning(
                f"Skipping command '{entry['command']}' at {entry['local_time']}: "
                f"delay too large ({delay}s > {self.MAX_EXECUTION_DELAY}s)"
            )
            return "skipped"

        # Execute command
        result = self._execute_command(cmd_info["load_id"], entry, scheduled_time, current_time)

        if result["success"]:
            entry["executed_at"] = current_time
            entry["execution_result"] = result

            # Save program after each execution
            self.save_program(program, base_dir)

            # Write to InfluxDB
            self._write_execution_to_influx(
                program["program_date"], cmd_info["load_id"], entry, result
            )
            return "executed"
        else:
            logger.error(
                f"Failed to execute command '{entry['command']}' "
                f"for {cmd_info['load_id']}: {result.get('error')}"
            )
            return "failed"

    def _collect_and_sort_commands(self, program: dict) -> list:
        """All commands of all loads as load_id, entry, timestamp, by time."""
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

        all_commands.sort(key=lambda x: x["timestamp"])
        return all_commands

    @staticmethod
    def due_pump_entry(programs: list, current_time: int) -> Optional[dict]:
        """The latest geothermal_pump entry at or before now across the given
        well-formed programs, so yesterday's last entry still counts."""
        due = [
            e
            for program in programs
            if program_is_well_formed(program) and PUMP_LOAD in program["loads"]
            for e in program["loads"][PUMP_LOAD]["schedule"]
            if e["timestamp"] <= current_time
        ]
        return max(due, key=lambda e: e["timestamp"]) if due else None

    def run_periodic_pulse(
        self,
        current_time: int,
        program: Optional[dict] = None,
        previous_program: Optional[dict] = None,
    ) -> bool:
        """EVU-pulse the pump once it has been ON too long. A due ALE or EVU
        newer than the pump's own ON never got applied, so apply it instead:
        a pulse would end in ON. Without a due entry, the pump decides."""
        if not hasattr(self.load_controller, "pump_controller"):
            return False
        pump_ctrl = self.load_controller.pump_controller
        if not pump_ctrl.check_evu_cycle_needed(current_time):
            return False
        due = self.due_pump_entry([previous_program, program], current_time)
        known = pump_ctrl.last_command_time
        if (
            due is not None
            and due["command"] != "ON"
            and (known is None or due["timestamp"] > known)
        ):
            logger.warning(f"Pump ON, but {due['command']} is due since {due['timestamp']}")
            result = pump_ctrl.execute_command(due["command"], due["timestamp"], current_time)
        else:
            logger.info("Periodic EVU cycle needed")
            result = pump_ctrl.perform_evu_cycle(current_time)
        if not result["success"]:
            logger.error(f"Periodic EVU check failed: {result['error']}")
            return False
        logger.info("Periodic EVU check completed successfully")
        return True

    def _update_program_summary(
        self,
        program: dict,
        all_commands: list,
        executed_count: int,
        skipped_count: int,
        failed_count: int,
        next_execution_time: Optional[int],
        evu_cycle_performed: bool,
        current_time: int,
    ) -> dict:
        """
        Update program state and generate execution summary.

        Args:
            program: Program dict to update
            all_commands: All commands list
            executed_count: Number of executed commands
            skipped_count: Number of skipped commands
            failed_count: Number of failed commands
            next_execution_time: Next scheduled command time
            evu_cycle_performed: Whether EVU cycle was performed
            current_time: Current time (epoch)

        Returns:
            Execution summary dict
        """
        total_commands = len([c for c in all_commands if not c["entry"].get("executed_at")])
        program["execution_status"] = {
            "executed_intervals": executed_count,
            "pending_intervals": total_commands - executed_count,
            "last_executed_timestamp": current_time if executed_count > 0 else None,
            "next_execution_timestamp": next_execution_time,
            "evu_cycle_performed": evu_cycle_performed,
        }

        return {
            "executed_count": executed_count,
            "skipped_count": skipped_count,
            "failed_count": failed_count,
            "next_execution_time": next_execution_time,
            "evu_cycle_performed": evu_cycle_performed,
        }

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
        """Write an executed command to the load_control bucket as an "actual" point."""
        if self.dry_run:
            logger.debug("DRY-RUN: Skipping InfluxDB write")
            return

        try:
            # A naive datetime is read as UTC, which shifts the point by the offset
            timestamp = datetime.datetime.fromtimestamp(
                result["actual_time"], tz=datetime.timezone.utc
            )
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

            # Get bucket name - supports both Config object and dict
            if hasattr(self.config, "influxdb_bucket_load_control"):
                bucket_name = self.config.influxdb_bucket_load_control
            else:
                bucket_name = self.config.get("influxdb_bucket_load_control")

            if not bucket_name:
                logger.error(
                    "INFLUXDB_BUCKET_LOAD_CONTROL is not configured! "
                    "Set it in .env to 'load_control_staging' (staging) or 'load_control' (production)"
                )
                raise ValueError("Missing required configuration: INFLUXDB_BUCKET_LOAD_CONTROL")

            self.influx.write_with_retry(bucket=bucket_name, record=point)

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
