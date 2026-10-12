#!/usr/bin/env python
"""Apply the heat pump state that is due now.

Runs at boot, because the relay board may not keep its state across a
reboot, and once at the heating cutover to take over from wibatemp.
"""

import argparse
import datetime
import subprocess
import time
from typing import Optional

from src.common.logger import setup_logger
from src.control.hardware_implementations import MockHardwareInterface
from src.control.program_fallback import load_valid_program
from src.control.pump_controller import PumpController
from src.monitoring.program_check import HELSINKI, PROGRAM_BASE_DIR, program_path

logger = setup_logger(__name__, "pump_restore.log")

PUMP_LOAD = "geothermal_pump"

# The Pi has no RTC: until NTP syncs, the clock shows the last shutdown.
# After the first wait the last known state is applied, and a later sync
# re-checks the program, because the executor skips commands over 30 min.
CLOCK_SYNC_TIMEOUT = 120
LATE_SYNC_TIMEOUT = 900
CLOCK_SYNC_POLL = 5


def due_command(now: int, base_dir: str) -> Optional[dict]:
    """The latest pump entry at or before now in today's or yesterday's
    program, so an ON block that started yesterday still counts."""
    today = datetime.datetime.fromtimestamp(now, HELSINKI).date()
    latest: Optional[dict] = None
    for day in (today - datetime.timedelta(days=1), today):
        program = load_valid_program(program_path(day, base_dir))
        if program is None or PUMP_LOAD not in program["loads"]:
            continue
        for entry in program["loads"][PUMP_LOAD]["schedule"]:
            if entry["timestamp"] <= now and (
                latest is None or entry["timestamp"] >= latest["timestamp"]
            ):
                latest = entry
    return latest


def clock_synchronized() -> bool:
    try:
        result = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Cannot read the clock sync state: {e}")
        return False
    return result.stdout.strip() == "yes"


def wait_for_clock_sync(timeout: int = CLOCK_SYNC_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if clock_synchronized():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(CLOCK_SYNC_POLL)


def choose_command(
    now: int, base_dir: str, controller: PumpController, clock_ok: bool
) -> Optional[str]:
    """A program entry newer than the last executed or manual command
    wins; otherwise that command. An unsynced clock trusts only the latter."""
    known_time = controller.last_command_time
    entry = due_command(now, base_dir) if clock_ok else None
    if entry is not None and (known_time is None or entry["timestamp"] > known_time):
        logger.info(f"Due by program: {entry['command']} since {entry['local_time']}")
        return entry["command"]
    if controller.last_command is not None:
        logger.info(f"Reapplying the last known command: {controller.last_command}")
        return controller.last_command
    return None


def restore(now: int, base_dir: str, controller: PumpController, clock_ok: bool) -> int:
    """Exit status 0 when a state was applied, 1 when none could be."""
    command = choose_command(now, base_dir, controller, clock_ok)
    if command is None:
        logger.error("No program entry is due and no last known state: nothing applied")
        return 1

    result = controller.apply_state(command, now, clock_ok)
    if not result["success"]:
        logger.error(f"Could not apply {command}: {result['error']}")
        return 1
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Apply the pump state due now")
    parser.add_argument("--dry-run", action="store_true", help="Use mock hardware")
    args = parser.parse_args(argv)

    controller = PumpController(dry_run=args.dry_run)
    if not args.dry_run and isinstance(controller.hardware, MockHardwareInterface):
        logger.error("Mock hardware without --dry-run: refusing to pretend a restore")
        return 1

    if wait_for_clock_sync(CLOCK_SYNC_TIMEOUT):
        return restore(int(time.time()), PROGRAM_BASE_DIR, controller, True)

    logger.warning(f"Clock not synchronized after {CLOCK_SYNC_TIMEOUT}s")
    first = restore(int(time.time()), PROGRAM_BASE_DIR, controller, False)
    if not wait_for_clock_sync(LATE_SYNC_TIMEOUT):
        logger.error("Clock never synchronized: the program was not checked")
        return 1
    now = int(time.time())
    command = choose_command(now, PROGRAM_BASE_DIR, controller, True)
    if command is not None and (first != 0 or command != controller.last_command):
        logger.warning(f"After the clock sync, applying {command}")
        return restore(now, PROGRAM_BASE_DIR, controller, True)
    return first
