#!/usr/bin/env python
"""Utility to replay JSON log files to InfluxDB for data recovery."""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

from src.common.json_logger import JSONDataLogger
from src.common.logger import setup_logger

logger = setup_logger(__name__)


def list_available_logs(data_source: Optional[str] = None, days: int = 30) -> dict[str, list[Path]]:
    """
    List available JSON log files.

    Args:
        data_source: Specific data source to list, or None for all
        days: Number of days to look back

    Returns:
        Dict mapping data source names to lists of log file paths
    """
    log_dir = Path("data_logs")

    if not log_dir.exists():
        logger.error(f"Log directory {log_dir} does not exist")
        return {}

    available_logs = {}

    if data_source:
        # List logs for specific data source
        json_logger = JSONDataLogger(data_source)
        logs = json_logger.get_recent_logs(days=days)
        if logs:
            available_logs[data_source] = logs
    else:
        # List logs for all data sources
        for source_dir in log_dir.iterdir():
            if source_dir.is_dir():
                source_name = source_dir.name
                json_logger = JSONDataLogger(source_name)
                logs = json_logger.get_recent_logs(days=days)
                if logs:
                    available_logs[source_name] = logs

    return available_logs


async def replay_log_file(log_file: Path, data_source: str, dry_run: bool = False) -> bool:
    """
    Replay a single JSON log file to InfluxDB.

    Args:
        log_file: Path to JSON log file
        data_source: Data source name (spot_prices, checkwatt, etc.)
        dry_run: If True, don't actually write to InfluxDB

    Returns:
        True if successful
    """
    logger.info(f"Processing log file: {log_file}")

    # Load log file
    json_logger = JSONDataLogger(data_source)
    log_entry = json_logger.load_log(log_file)

    if not log_entry:
        logger.error(f"Failed to load log file: {log_file}")
        return False

    # Extract data and metadata
    data = log_entry.get("data")
    metadata = log_entry.get("metadata", {})
    timestamp = log_entry.get("timestamp")

    logger.info(f"Log timestamp: {timestamp}")
    logger.info(f"Metadata: {metadata}")

    if dry_run:
        logger.info(f"[DRY-RUN] Would replay data from {log_file}")
        logger.info(f"[DRY-RUN] Data source: {data_source}")
        return True

    # Import and call appropriate collector function
    try:
        if data_source == "spot_prices":
            from src.common.config import get_config
            from src.data_collection.spot_prices import (
                process_spot_prices,
                write_spot_prices_to_influx,
            )

            config = get_config()
            processed = process_spot_prices(data, config)
            latest_timestamp = await write_spot_prices_to_influx(processed)
            success = latest_timestamp is not None

        elif data_source == "checkwatt":
            from src.data_collection.checkwatt import (
                process_checkwatt_data,
                write_checkwatt_to_influx,
            )

            processed = process_checkwatt_data(data)
            success = await write_checkwatt_to_influx(processed)

        elif data_source == "weather":
            import datetime

            from src.data_collection.weather import write_weather_to_influx

            # Convert ISO string keys back to datetime
            weather_data = {
                datetime.datetime.fromisoformat(ts): fields for ts, fields in data.items()
            }
            success = write_weather_to_influx(weather_data)

        elif data_source == "windpower":
            from src.data_collection.windpower import (
                process_windpower_data,
                write_windpower_to_influx,
            )

            processed = process_windpower_data(data)
            latest_timestamp = await write_windpower_to_influx(processed)
            success = latest_timestamp is not None

        elif data_source == "energy_meter":
            logger.error(
                "Energy meter logs require previous measurement context "
                "and cannot be replayed automatically"
            )
            return False

        elif data_source == "temperature":
            from src.data_collection.temperature import write_temperatures_to_influx

            success = write_temperatures_to_influx(data)

        else:
            logger.error(f"Unknown data source: {data_source}")
            return False

        if success:
            logger.info(f"Successfully replayed data from {log_file}")
        else:
            logger.error(f"Failed to replay data from {log_file}")

        return success

    except Exception as e:
        logger.error(f"Exception replaying log file {log_file}: {e}")
        import traceback

        traceback.print_exc()
        return False


async def replay_logs(
    data_source: str, days: int = 7, dry_run: bool = False, limit: Optional[int] = None
) -> tuple[int, int]:
    """
    Replay JSON logs for a data source.

    Args:
        data_source: Data source name
        days: Number of days to look back
        dry_run: If True, don't actually write to InfluxDB
        limit: Maximum number of files to replay

    Returns:
        Tuple of (success_count, failure_count)
    """
    logger.info(f"Replaying logs for {data_source} (last {days} days)")

    json_logger = JSONDataLogger(data_source)
    log_files = json_logger.get_recent_logs(days=days)

    if not log_files:
        logger.warning(f"No log files found for {data_source}")
        return 0, 0

    logger.info(f"Found {len(log_files)} log files")

    if limit:
        log_files = log_files[:limit]
        logger.info(f"Processing first {limit} files only")

    success_count = 0
    failure_count = 0

    for log_file in log_files:
        try:
            success = await replay_log_file(log_file, data_source, dry_run=dry_run)
            if success:
                success_count += 1
            else:
                failure_count += 1
        except Exception as e:
            logger.error(f"Exception processing {log_file}: {e}")
            failure_count += 1

    logger.info(f"Replay complete: {success_count} succeeded, {failure_count} failed")
    return success_count, failure_count


def main():
    """Main entry point for JSON log replay utility."""
    parser = argparse.ArgumentParser(
        description="Replay JSON log files to InfluxDB for data recovery",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List available logs for all data sources
  python -m src.tools.replay_json_logs --list

  # List logs for specific data source
  python -m src.tools.replay_json_logs --list --source spot_prices

  # Replay spot price logs from last 7 days (dry-run)
  python -m src.tools.replay_json_logs --source spot_prices --dry-run

  # Replay checkwatt logs from last 30 days
  python -m src.tools.replay_json_logs --source checkwatt --days 30

  # Replay only 5 most recent weather logs
  python -m src.tools.replay_json_logs --source weather --limit 5
        """,
    )
    parser.add_argument("--list", action="store_true", help="List available log files")
    parser.add_argument("--source", help="Data source name to replay")
    parser.add_argument(
        "--days", type=int, default=7, help="Number of days to look back (default: 7)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be replayed without writing"
    )
    parser.add_argument("--limit", type=int, help="Limit number of files to replay")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    if args.verbose:
        import logging

        logger.setLevel(logging.DEBUG)

    if args.list:
        # List available logs
        available_logs = list_available_logs(data_source=args.source, days=args.days)

        if not available_logs:
            print("No log files found")
            return 0

        for source_name, log_files in available_logs.items():
            print(f"\n{source_name}: {len(log_files)} log files")
            for log_file in log_files[:10]:  # Show first 10
                mtime = log_file.stat().st_mtime
                import datetime

                mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                print(f"  {log_file.name} ({mtime_str})")
            if len(log_files) > 10:
                print(f"  ... and {len(log_files) - 10} more")

        return 0

    if not args.source:
        print("Error: --source is required (unless using --list)")
        parser.print_help()
        return 1

    # Replay logs
    try:
        success_count, failure_count = asyncio.run(
            replay_logs(
                data_source=args.source,
                days=args.days,
                dry_run=args.dry_run,
                limit=args.limit,
            )
        )

        if failure_count > 0:
            return 1
        else:
            return 0

    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
