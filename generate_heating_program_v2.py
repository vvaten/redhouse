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
import sys
from pathlib import Path

from src.common.logger import setup_logger
from src.control.program_generator import HeatingProgramGenerator

logger = setup_logger(__name__)


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

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Set log level
    if args.verbose:
        logger.setLevel("DEBUG")

    logger.info("=" * 60)
    logger.info("Starting heating program generation")
    logger.info("=" * 60)

    try:
        # Initialize generator
        generator = HeatingProgramGenerator()

        # Generate program
        logger.info(f"Generating program for date_offset={args.date_offset}")
        if args.base_date:
            logger.info(f"Using base date: {args.base_date}")

        program = generator.generate_daily_program(
            date_offset=args.date_offset,
            simulation_mode=args.simulation,
            base_date=args.base_date,
        )

        logger.info(f"Generated program for {program['program_date']}")
        logger.info(
            f"Average temperature: {program['input_parameters']['avg_temperature_c']:.1f}C"
        )
        logger.info(
            f"Required heating hours: {program['planning_results']['total_heating_hours_needed']:.2f}h"
        )
        logger.info(
            f"Estimated cost: {program['planning_results']['estimated_total_cost_eur']:.2f} EUR"
        )

        # Save to JSON
        json_path = generator.save_program_json(program, output_dir=args.output_dir)
        logger.info(f"Saved program to: {json_path}")

        # Save to InfluxDB (unless dry-run)
        if not args.dry_run:
            generator.save_program_influxdb(program, data_type="plan")
            logger.info("Saved program to InfluxDB")
        else:
            logger.info("Dry-run: Skipped InfluxDB save")

        # Print summary
        print("\n" + "=" * 60)
        print(f"Heating Program for {program['program_date']}")
        print("=" * 60)
        print(f"Average Temperature: {program['input_parameters']['avg_temperature_c']:.1f}C")
        print(
            f"Heating Hours Needed: {program['planning_results']['total_heating_hours_needed']:.2f}h"
        )
        print(
            f"EVU-OFF Intervals: {program['planning_results']['total_evu_off_intervals']}"
        )
        print(f"Estimated Cost: {program['planning_results']['estimated_total_cost_eur']:.2f} EUR")
        print(
            f"Price Range: {program['planning_results']['cheapest_interval_price']:.2f} - "
            f"{program['planning_results']['most_expensive_interval_price']:.2f} c/kWh"
        )
        print(f"\nJSON File: {json_path}")
        print("=" * 60)

        # Print load schedules
        for load_id, load_data in program["loads"].items():
            if load_data["total_intervals_on"] > 0:
                print(f"\n{load_id.upper()}:")
                print(f"  Intervals ON: {load_data['total_intervals_on']}")
                print(f"  Total Hours: {load_data['total_hours_on']:.2f}h")
                print(f"  Estimated Cost: {load_data['estimated_cost_eur']:.2f} EUR")
                print(f"  Power: {load_data['power_kw']:.1f} kW")
                print(f"\n  Schedule ({len(load_data['schedule'])} entries):")
                for entry in load_data["schedule"][:5]:  # Show first 5
                    print(
                        f"    {entry['local_time'][:19]} -> {entry['command']} "
                        f"({entry['reason']})"
                    )
                if len(load_data["schedule"]) > 5:
                    print(f"    ... and {len(load_data['schedule']) - 5} more entries")

        logger.info("Heating program generation completed successfully")
        return 0

    except Exception as e:
        logger.error(f"Failed to generate heating program: {e}", exc_info=True)
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
