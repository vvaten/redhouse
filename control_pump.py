#!/usr/bin/env python3
"""
Manual pump control CLI tool.

Usage:
    python control_pump.py ON     # Turn pump ON
    python control_pump.py ALE    # Set to ALE mode (lower temp)
    python control_pump.py EVU    # Turn pump OFF (EVU mode)
    python control_pump.py status # Show current status
"""

import argparse
import sys
import time

from src.common.logger import setup_logger
from src.control.pump_controller import PumpController

logger = setup_logger(__name__, "pump_control.log")


def main():
    parser = argparse.ArgumentParser(
        description="Manual geothermal pump control",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "command",
        choices=["ON", "ALE", "EVU", "status"],
        help="Command to execute (ON/ALE/EVU) or 'status' to show current state",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate command without actually executing",
    )

    args = parser.parse_args()

    # Initialize controller
    controller = PumpController(dry_run=args.dry_run)

    if args.command == "status":
        # Show status
        print(f"\n{'='*50}")
        print("Geothermal Pump Status")
        print(f"{'='*50}")
        print(f"Last command: {controller.last_command or 'Unknown'}")
        if controller.last_command_time:
            print(f"Last command time: {time.ctime(controller.last_command_time)}")
        print(
            f"ON time accumulated: {controller.on_time_accumulated}s ({controller.on_time_accumulated/60:.1f} min)"
        )
        if controller.last_evu_cycle_time:
            print(f"Last EVU cycle: {time.ctime(controller.last_evu_cycle_time)}")
        print(f"EVU cycle threshold: {controller.EVU_CYCLE_THRESHOLD/60:.0f} min")
        needs_cycle = controller.check_evu_cycle_needed()
        print(f"Needs EVU cycle: {'YES' if needs_cycle else 'No'}")
        print(f"{'='*50}\n")
        return 0

    # Execute command
    current_time = int(time.time())

    print(f"\n{'='*50}")
    print(f"Executing: {args.command}")
    print(f"{'='*50}")

    if args.dry_run:
        print("[DRY-RUN MODE - No actual hardware control]")

    try:
        result = controller.execute_command(
            args.command,
            scheduled_time=current_time,
            actual_time=current_time,
        )

        if result["success"]:
            print(f"[SUCCESS] {result['output']}")
            print(f"\nPump is now in '{args.command}' mode")
            logger.info(f"Manual pump control: {args.command} - SUCCESS")
            return 0
        else:
            print(f"[FAILED] {result.get('error', 'Unknown error')}")
            logger.error(f"Manual pump control: {args.command} - FAILED: {result.get('error')}")
            return 1

    except Exception as e:
        print(f"[ERROR] {e}")
        logger.error(f"Manual pump control: {args.command} - EXCEPTION: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
