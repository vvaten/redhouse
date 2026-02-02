#!/usr/bin/env python
"""
Execute daily heating program - wrapper script for cron/systemd.

This script executes today's heating program by:
- Loading the program JSON
- Executing pending commands
- Marking commands as executed
- Writing actual execution to InfluxDB

Usage:
    python execute_heating_program_v2.py [OPTIONS]

Options:
    --date DATE         Execute program for specific date (YYYY-MM-DD, default: today)
    --base-dir PATH     Base directory for program files (default: current dir)
    --dry-run           Log commands but don't execute
    --verbose           Enable verbose logging
    --force             Force execution of old commands (ignore max delay)

Examples:
    # Normal operation (run every 15 minutes via cron)
    python execute_heating_program_v2.py

    # Dry run (test without executing)
    python execute_heating_program_v2.py --dry-run

    # Execute specific date
    python execute_heating_program_v2.py --date 2025-01-15

    # Verbose logging
    python execute_heating_program_v2.py --verbose
"""

import argparse
import datetime
import sys

from src.common.logger import setup_logger
from src.control.program_executor import HeatingProgramExecutor

logger = setup_logger(__name__)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Execute daily heating program",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--date",
        type=str,
        help="Execute program for specific date (YYYY-MM-DD, default: today)",
    )

    parser.add_argument(
        "--base-dir",
        type=str,
        default=".",
        help="Base directory for program files (default: current directory)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log commands but don't execute",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Force execution of old commands (ignore max delay)",
    )

    return parser.parse_args()


def _load_program_with_error_handling(executor, program_date: str, base_dir: str):
    """Load program and provide helpful error messages on failure."""
    try:
        return executor.load_program(program_date=program_date, base_dir=base_dir)
    except FileNotFoundError as e:
        logger.error(f"Program file not found: {e}")
        print(f"\nERROR: Program file not found for {program_date}", file=sys.stderr)
        print(f"Expected location: {base_dir}/{program_date[:7]}/", file=sys.stderr)
        print("\nHave you generated the program? Run:", file=sys.stderr)
        print("  python generate_heating_program_v2.py", file=sys.stderr)
        return None


def _handle_day_transition_if_needed(executor, program, base_dir: str):
    """Handle day transition if in the first 15 minutes of the day."""
    now = datetime.datetime.now()
    if now.hour == 0 and now.minute < 15:
        yesterday_date = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            yesterday_program = executor.load_program(
                program_date=yesterday_date, base_dir=base_dir
            )
            program = executor.handle_day_transition(program, yesterday_program)
            logger.info("Day transition handled")
        except FileNotFoundError:
            logger.warning(f"Yesterday's program not found: {yesterday_date}")
    return program


def _print_execution_summary(summary: dict, program_date: str, dry_run: bool):
    """Print execution summary with statistics and next execution time."""
    print("\n" + "=" * 60)
    print(f"Execution Summary for {program_date}")
    print("=" * 60)
    print(f"Executed: {summary['executed_count']} commands")
    print(f"Skipped:  {summary['skipped_count']} commands (delay too large)")
    print(f"Failed:   {summary['failed_count']} commands")

    if summary["next_execution_time"]:
        next_time = datetime.datetime.fromtimestamp(summary["next_execution_time"])
        time_until = summary["next_execution_time"] - int(datetime.datetime.now().timestamp())
        print(f"\nNext execution: {next_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Time until next: {time_until // 60} minutes")
    else:
        print("\nNo more commands scheduled for today")

    print("=" * 60)

    if dry_run:
        print("\nDRY-RUN MODE: No commands were actually executed")


def main():
    """Main entry point."""
    args = parse_args()

    if args.verbose:
        logger.setLevel("DEBUG")

    logger.info("=" * 60)
    logger.info("Starting heating program execution")
    logger.info("=" * 60)

    try:
        executor = HeatingProgramExecutor(dry_run=args.dry_run)

        # Determine date
        program_date = args.date if args.date else datetime.date.today().strftime("%Y-%m-%d")
        logger.info(f"Executing program for {'specified date' if args.date else 'today'}: {program_date}")

        # Load program
        program = _load_program_with_error_handling(executor, program_date, args.base_dir)
        if program is None:
            return 1

        # Handle day transition
        program = _handle_day_transition_if_needed(executor, program, args.base_dir)

        # Apply force flag
        if args.force:
            executor.MAX_EXECUTION_DELAY = 86400 * 7
            logger.warning("Force mode: Ignoring max execution delay")

        # Execute program
        summary = executor.execute_program(program, base_dir=args.base_dir)

        # Print summary
        _print_execution_summary(summary, program_date, args.dry_run)

        # Check for failures
        if summary["failed_count"] > 0:
            logger.error(f"{summary['failed_count']} commands failed to execute")
            return 1

        logger.info("Heating program execution completed successfully")
        return 0

    except Exception as e:
        logger.error(f"Failed to execute heating program: {e}", exc_info=True)
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
