#!/usr/bin/env python
"""
Generate daily heating program - wrapper script for cron/systemd.

This script generates tomorrow's heating program and saves it to:
- JSON file: YYYY-MM/heating_program_schedule_YYYY-MM-DD.json
- InfluxDB: load_control bucket

Usage:
    python generate_heating_program_v2.py [OPTIONS]

Options:
    --date-offset N     Generate program for N days from today (default: 1 = tomorrow)
    --output-dir PATH   Output directory for JSON files (default: current dir)
    --dry-run           Generate program but don't save to InfluxDB
    --verbose           Enable verbose logging
    --simulation        Mark as simulation mode
    --base-date DATE    Base date for historical simulation (YYYY-MM-DD)
    --if-missing        Skip if a generated program for that day exists

Examples:
    # Generate tomorrow's program (normal operation)
    python generate_heating_program_v2.py

    # Generate for 2 days from now
    python generate_heating_program_v2.py --date-offset 2

    # Dry run (no InfluxDB write)
    python generate_heating_program_v2.py --dry-run

    # Historical simulation
    python generate_heating_program_v2.py --base-date 2024-10-15 --simulation
"""

import argparse
import datetime
import sys
import time
from typing import Optional

from src.common.atomic_json import write_json
from src.common.control_lock import control_lock
from src.common.local_time import helsinki_date
from src.common.logger import setup_logger
from src.control.program_fallback import is_fallback_copy, load_valid_program
from src.control.program_generator import HeatingProgramGenerator
from src.control.program_shape import PUMP_LOAD
from src.control.quarter_program import revise_today
from src.monitoring.program_check import program_path

logger = setup_logger(__name__)
# The executor holds the lock for a run of a few seconds at each quarter
REVISION_LOCK_TIMEOUT_S = 60
REVISION_MARGIN_S = 60


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate daily heating program",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--date-offset",
        type=int,
        default=1,
        help="Day offset from today (1 = tomorrow, 0 = today, default: 1)",
    )

    parser.add_argument(
        "--output-dir",
        type=str,
        default=".",
        help="Output directory for JSON files (default: current directory)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate program but don't save to InfluxDB",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    parser.add_argument(
        "--simulation",
        action="store_true",
        help="Mark as simulation mode",
    )

    parser.add_argument(
        "--base-date",
        type=str,
        help="Base date for historical simulation (YYYY-MM-DD)",
    )

    parser.add_argument(
        "--if-missing",
        action="store_true",
        help="Do nothing if a generated program for that day already exists",
    )

    return parser.parse_args()


# Set in the JSON when the InfluxDB plan write failed, so a retry redoes both.
PLAN_NOT_IN_INFLUX_KEY = "plan_not_in_influx"


def generated_program_exists(date_offset: int, output_dir: str) -> bool:
    """Whether a complete generated program is there. A fallback copy, an
    unusable file or a plan missing from InfluxDB counts as not there."""
    program_date = helsinki_date(date_offset)
    program = load_valid_program(program_path(program_date, output_dir))
    if program is None:
        return False
    return not is_fallback_copy(program) and PLAN_NOT_IN_INFLUX_KEY not in program


def _log_program_generation_info(program: dict, args):
    """Log information about the generated program."""
    logger.info(f"Generated program for {program['program_date']}")
    logger.info(f"Average temperature: {program['input_parameters']['avg_temperature_c']:.1f}C")
    logger.info(
        f"Required heating hours: {program['planning_results']['total_heating_hours_needed']:.2f}h"
    )
    logger.info(
        f"Estimated cost: {program['planning_results']['estimated_total_cost_eur']:.2f} EUR"
    )


def _save_program_files(generator, program: dict, output_dir: str, dry_run: bool):
    """Save program to JSON and optionally to InfluxDB. The JSON is saved
    even when InfluxDB fails, because the executor needs only the JSON."""
    if dry_run:
        logger.info("Dry-run: Skipped InfluxDB save")
    else:
        try:
            generator.save_program_influxdb(program, data_type="plan")
            logger.info("Saved program to InfluxDB")
        except Exception:
            program[PLAN_NOT_IN_INFLUX_KEY] = True
            generator.save_program_json(program, output_dir=output_dir)
            raise

    json_path = generator.save_program_json(program, output_dir=output_dir)
    logger.info(f"Saved program to: {json_path}")
    return json_path


def _save_today_revision(generator, output_dir: str) -> Optional[dict]:
    """Lay the re-planned rest of today onto a fresh read of today's file,
    under the control lock the executor holds while it writes the file.
    Raises when the window start is too near: the next run re-plans."""
    part = generator.today_part
    if part is None:
        return None
    start = part.start
    path = program_path(datetime.date.fromisoformat(part.program_date), output_dir)
    with control_lock(REVISION_LOCK_TIMEOUT_S):
        if time.time() > start - REVISION_MARGIN_S:
            raise RuntimeError("Too late to re-plan the rest of today; the next run re-plans")
        fresh = load_valid_program(path)
        if fresh is None:
            raise RuntimeError(f"Today's program {path} is gone or unusable")
        revision = revise_today(fresh, PUMP_LOAD, part.entries, start, part.moved)
        write_json(path, revision)
    logger.info(f"Re-planned the rest of today: {path}")
    return revision


def _save_revision_influxdb(generator, revision: Optional[dict], dry_run: bool) -> None:
    """The revision as "adjusted", so the first plan stays comparable."""
    if revision is None or dry_run:
        return
    try:
        generator.save_program_influxdb(revision, data_type="adjusted")
    except Exception as e:
        logger.error(f"Today's re-plan not saved to InfluxDB: {e}")


def _print_program_summary(program: dict, json_path: str):
    """Print comprehensive program summary."""
    print("\n" + "=" * 60)
    print(f"Heating Program for {program['program_date']}")
    print("=" * 60)
    print(f"Average Temperature: {program['input_parameters']['avg_temperature_c']:.1f}C")
    print(f"Heating Hours Needed: {program['planning_results']['total_heating_hours_needed']:.2f}h")
    print(f"EVU-OFF Intervals: {program['planning_results']['total_evu_off_intervals']}")
    print(f"Estimated Cost: {program['planning_results']['estimated_total_cost_eur']:.2f} EUR")
    print(
        f"Price Range: {program['planning_results']['cheapest_interval_price']:.2f} - "
        f"{program['planning_results']['most_expensive_interval_price']:.2f} c/kWh"
    )
    print(f"\nJSON File: {json_path}")
    print("=" * 60)


def _print_load_schedules(program: dict):
    """Print detailed schedule for each active load."""
    for load_id, load_data in program["loads"].items():
        if load_data["total_intervals_on"] > 0:
            print(f"\n{load_id.upper()}:")
            print(f"  Intervals ON: {load_data['total_intervals_on']}")
            print(f"  Total Hours: {load_data['total_hours_on']:.2f}h")
            print(f"  Estimated Cost: {load_data['estimated_cost_eur']:.2f} EUR")
            print(f"  Power: {load_data['power_kw']:.1f} kW")
            print(f"\n  Schedule ({len(load_data['schedule'])} entries):")
            for entry in load_data["schedule"]:
                print(
                    f"    {entry['local_time'][:19]} -> {entry['command']} " f"({entry['reason']})"
                )


def main():
    """Main entry point."""
    args = parse_args()

    if args.verbose:
        logger.setLevel("DEBUG")

    logger.info("=" * 60)
    logger.info("Starting heating program generation")
    logger.info("=" * 60)

    if args.if_missing and not args.base_date:
        if generated_program_exists(args.date_offset, args.output_dir):
            logger.info("Program already generated, nothing to do")
            return 0

    try:
        generator = HeatingProgramGenerator(program_dir=args.output_dir)

        # Generate program
        logger.info(f"Generating program for date_offset={args.date_offset}")
        if args.base_date:
            logger.info(f"Using base date: {args.base_date}")

        program = generator.generate_daily_program(
            date_offset=args.date_offset,
            simulation_mode=args.simulation,
            base_date=args.base_date,
        )

        _log_program_generation_info(program, args)

        # Today's re-plan first: tomorrow's program counts on it
        revision = _save_today_revision(generator, args.output_dir)
        json_path = _save_program_files(generator, program, args.output_dir, args.dry_run)
        _save_revision_influxdb(generator, revision, args.dry_run)

        # Print summary
        _print_program_summary(program, json_path)
        _print_load_schedules(program)

        logger.info("Heating program generation completed successfully")
        return 0

    except Exception as e:
        logger.error(f"Failed to generate heating program: {e}", exc_info=True)
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
