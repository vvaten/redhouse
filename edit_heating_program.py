#!/usr/bin/env python
"""
Simple heating program editor for manual adjustments.

Usage:
    python edit_heating_program.py [today|tomorrow|latest] [OPTIONS]

    Arguments:
        today/tomorrow/latest   Which program to edit (default: latest)

    Options:
        --list                  Display the current program
        --start HH:MM           Start time for new entry
        --end HH:MM             End time for new entry
        --mode ON|EVU|ALE       Command mode for new entry

Examples:
    # List today's program
    python edit_heating_program.py today --list

    # Add ON period from 11:00 to 12:00 to tomorrow's program
    python edit_heating_program.py tomorrow --start 11:00 --end 12:00 --mode ON

    # Add EVU-OFF period to latest program
    python edit_heating_program.py latest --start 14:00 --end 17:00 --mode EVU
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


def find_program_file(target: str) -> Optional[Path]:
    """
    Find heating program file based on target (today/tomorrow/latest).

    Args:
        target: 'today', 'tomorrow', or 'latest'

    Returns:
        Path to program file, or None if not found
    """
    if target == "today":
        date = datetime.now().strftime("%Y-%m-%d")
    elif target == "tomorrow":
        date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    else:  # latest
        # Find most recent program file
        pattern = "heating_program_schedule_*.json"
        program_files = sorted(Path(".").glob(f"*/{pattern}"))
        if not program_files:
            program_files = sorted(Path(".").glob(pattern))
        if program_files:
            return program_files[-1]
        return None

    # Look for specific date
    year_month = date[:7]  # YYYY-MM
    filename = f"heating_program_schedule_{date}.json"
    filepath = Path(year_month) / filename

    if filepath.exists():
        return filepath

    # Try current directory
    filepath = Path(filename)
    if filepath.exists():
        return filepath

    return None


def load_program(filepath: Path) -> dict:
    """Load heating program from JSON file."""
    with open(filepath) as f:
        return json.load(f)


def save_program(filepath: Path, program: dict):
    """Save heating program to JSON file."""
    with open(filepath, "w") as f:
        json.dump(program, f, indent=2)


def list_program(program: dict):
    """Display heating program in readable format."""
    print("=" * 70)
    print(f"Heating Program: {program['program_date']}")
    print("=" * 70)
    print(f"Generated: {program['generated_at']}")
    print(f"Average Temperature: {program['input_parameters']['avg_temperature_c']:.1f}C")
    print(f"Heating Hours Needed: {program['planning_results']['total_heating_hours_needed']:.1f}h")
    print(f"Estimated Cost: {program['planning_results']['estimated_total_cost_eur']:.2f} EUR")
    print()

    for load_id, load_data in program["loads"].items():
        if load_data["total_intervals_on"] > 0 or load_data["schedule"]:
            print(f"{load_id.upper()}:")
            print(f"  Power: {load_data['power_kw']:.1f} kW")
            print(f"  Total Hours: {load_data['total_hours_on']:.2f}h")
            print(f"  Estimated Cost: {load_data['estimated_cost_eur']:.2f} EUR")
            print(f"\n  Schedule ({len(load_data['schedule'])} entries):")

            for entry in load_data["schedule"]:
                time_str = entry["local_time"][:16]  # YYYY-MM-DD HH:MM
                cmd = entry["command"]
                reason = entry.get("reason", "")

                if entry.get("duration_minutes"):
                    duration = f"{entry['duration_minutes']}min"
                else:
                    duration = "until_next"

                print(f"    {time_str} -> {cmd:3s} ({duration:10s}) {reason}")

            print()

    print("=" * 70)


def _parse_time_and_validate(program_date, start_time: str, end_time: str):
    """Parse start/end times and validate duration."""
    start_hour, start_min = map(int, start_time.split(":"))
    end_hour, end_min = map(int, end_time.split(":"))

    start_dt = program_date.replace(hour=start_hour, minute=start_min, second=0)
    end_dt = program_date.replace(hour=end_hour, minute=end_min, second=0)

    duration_minutes = int((end_dt - start_dt).total_seconds() / 60)

    if duration_minutes <= 0:
        raise ValueError("End time must be after start time")

    return start_dt, end_dt, duration_minutes


def _create_schedule_entry(start_dt, duration_minutes: int, mode: str):
    """Create a new schedule entry with all required fields."""
    return {
        "timestamp": int(start_dt.timestamp()),
        "utc_time": start_dt.astimezone(None).isoformat(),
        "local_time": start_dt.isoformat(),
        "command": mode,
        "duration_minutes": duration_minutes,
        "reason": "manual_edit",
        "spot_price_total_c_kwh": None,
        "solar_prediction_kwh": None,
        "priority_score": None,
    }


def _remove_overlapping_entries(schedule: list, new_start: int, new_end: int):
    """Remove entries that overlap with the new time range."""
    filtered_schedule = []
    removed_count = 0

    for entry in schedule:
        entry_start = entry["timestamp"]

        if entry.get("duration_minutes"):
            entry_end = entry_start + (entry["duration_minutes"] * 60)
        else:
            # ALE entries have no duration, treat as instant transition
            entry_end = entry_start + 1

        # Check if entry overlaps: entry_start < new_end AND entry_end > new_start
        overlaps = entry_start < new_end and entry_end > new_start

        if not overlaps:
            filtered_schedule.append(entry)
        else:
            removed_count += 1

    return filtered_schedule, removed_count


def add_entry(program: dict, start_time: str, end_time: str, mode: str) -> dict:
    """
    Add a new entry to the heating program.

    Args:
        program: Heating program dict
        start_time: Start time in HH:MM format
        end_time: End time in HH:MM format
        mode: Command mode (ON, EVU, ALE)

    Returns:
        Modified program dict
    """
    program_date = datetime.fromisoformat(program["program_date"])
    start_dt, end_dt, duration_minutes = _parse_time_and_validate(
        program_date, start_time, end_time
    )

    new_entry = _create_schedule_entry(start_dt, duration_minutes, mode)

    # Add to first load (geothermal_pump typically)
    load_id = list(program["loads"].keys())[0]
    schedule = program["loads"][load_id]["schedule"]

    # Calculate new entry's time range
    new_start = int(start_dt.timestamp())
    new_end = int(end_dt.timestamp())

    # Remove overlapping entries and add new entry
    filtered_schedule, removed_count = _remove_overlapping_entries(schedule, new_start, new_end)
    filtered_schedule.append(new_entry)
    filtered_schedule.sort(key=lambda x: x["timestamp"])

    # Update schedule
    program["loads"][load_id]["schedule"] = filtered_schedule

    if removed_count > 0:
        print(f"Removed {removed_count} overlapping entry/entries")
    print(f"Added {mode} entry: {start_time} - {end_time} ({duration_minutes} minutes)")

    return program


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Simple heating program editor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "target",
        nargs="?",
        default="latest",
        choices=["today", "tomorrow", "latest"],
        help="Which program to edit (default: latest)",
    )

    parser.add_argument(
        "--list",
        action="store_true",
        help="Display the current program",
    )

    parser.add_argument(
        "--start",
        type=str,
        help="Start time for new entry (HH:MM)",
    )

    parser.add_argument(
        "--end",
        type=str,
        help="End time for new entry (HH:MM)",
    )

    parser.add_argument(
        "--mode",
        type=str,
        choices=["ON", "EVU", "ALE"],
        help="Command mode for new entry",
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Find program file
    filepath = find_program_file(args.target)
    if not filepath:
        print(f"ERROR: No heating program found for '{args.target}'", file=sys.stderr)
        return 1

    print(f"Program file: {filepath}")
    print()

    # Load program
    try:
        program = load_program(filepath)
    except Exception as e:
        print(f"ERROR: Failed to load program: {e}", file=sys.stderr)
        return 1

    # List mode
    if args.list:
        list_program(program)
        return 0

    # Edit mode
    if args.start and args.end and args.mode:
        try:
            program = add_entry(program, args.start, args.end, args.mode)
            save_program(filepath, program)
            print(f"Program saved to {filepath}")
            print()
            print("Updated schedule:")
            list_program(program)
            return 0
        except Exception as e:
            print(f"ERROR: Failed to edit program: {e}", file=sys.stderr)
            return 1

    # No action specified
    if not args.list and not (args.start and args.end and args.mode):
        print("No action specified. Use --list to view or provide --start, --end, --mode to edit.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
