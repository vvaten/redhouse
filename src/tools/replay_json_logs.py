#!/usr/bin/env python
"""Utility to replay JSON log files to InfluxDB for data recovery."""

import argparse
import asyncio
import datetime
import inspect
import logging
import sys
import traceback
from pathlib import Path
from typing import Optional

import pytz

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


def _load_and_validate_log(log_file: Path, data_source: str):
    """Load log file and validate contents. Returns (data, timestamp) tuple."""
    json_logger = JSONDataLogger(data_source)
    log_entry = json_logger.load_log(log_file)

    if not log_entry:
        logger.error(f"Failed to load log file: {log_file}")
        return None, None

    data = log_entry.get("data")
    metadata = log_entry.get("metadata", {})
    timestamp = log_entry.get("timestamp")

    logger.info(f"Log timestamp: {timestamp}")
    logger.info(f"Metadata: {metadata}")

    return data, timestamp


async def _replay_spot_prices(data):
    """Replay spot prices data to InfluxDB."""
    from src.common.config import get_config
    from src.data_collection.spot_prices import process_spot_prices, write_spot_prices_to_influx

    config = get_config()
    processed = process_spot_prices(data, config)
    latest_timestamp = await write_spot_prices_to_influx(processed)
    return latest_timestamp is not None


async def _replay_checkwatt(data):
    """Replay checkwatt data to InfluxDB."""
    from src.data_collection.checkwatt import process_checkwatt_data, write_checkwatt_to_influx

    processed = process_checkwatt_data(data)
    return await write_checkwatt_to_influx(processed)


def _replay_weather(data):
    """Replay weather data to InfluxDB."""

    from src.data_collection.weather import write_weather_to_influx

    weather_data = {datetime.datetime.fromisoformat(ts): fields for ts, fields in data.items()}
    return write_weather_to_influx(weather_data)


async def _replay_windpower(data):
    """Replay windpower data to InfluxDB."""
    from src.data_collection.windpower import process_windpower_data, write_windpower_to_influx

    processed = process_windpower_data(data)
    latest_timestamp = await write_windpower_to_influx(processed)
    return latest_timestamp is not None


def _replay_temperature(data, timestamp=None):
    """Replay temperature data to InfluxDB."""
    from src.data_collection.temperature import write_temperatures_to_influx

    return write_temperatures_to_influx(data, timestamp=timestamp)


async def _replay_shelly_em3(data):
    """Replay Shelly EM3 data to InfluxDB."""
    from src.data_collection.shelly_em3 import process_shelly_em3_data, write_shelly_em3_to_influx

    if not data:
        logger.error("No data to replay")
        return False

    processed = process_shelly_em3_data(data)
    return await write_shelly_em3_to_influx(processed)


# Handler registry mapping data sources to replay functions
REPLAY_HANDLERS = {
    "spot_prices": _replay_spot_prices,
    "checkwatt": _replay_checkwatt,
    "weather": _replay_weather,
    "windpower": _replay_windpower,
    "temperature": _replay_temperature,
    "shelly_em3": _replay_shelly_em3,
}


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

    data, timestamp = _load_and_validate_log(log_file, data_source)
    if data is None:
        return False

    # Parse timestamp string to datetime for handlers that need it.
    # Log timestamps are naive local time. Convert to naive UTC to match
    # how write_temperatures_to_influx normally writes (datetime.utcnow()).
    log_timestamp = None
    if timestamp:
        try:
            local_tz = pytz.timezone("Europe/Helsinki")
            log_timestamp = datetime.datetime.fromisoformat(timestamp)
            if log_timestamp.tzinfo is None:
                # Naive timestamp = local time on Pi
                log_timestamp = local_tz.localize(log_timestamp)
            # Convert to naive UTC (matching utcnow() format)
            log_timestamp = log_timestamp.astimezone(pytz.utc).replace(tzinfo=None)
        except (ValueError, TypeError) as e:
            logger.warning(f"Could not parse timestamp: {timestamp}: {e}")

    if dry_run:
        logger.info(f"[DRY-RUN] Would replay data from {log_file}")
        logger.info(f"[DRY-RUN] Data source: {data_source}, timestamp: {log_timestamp}")
        return True

    # Dispatch to appropriate handler
    try:
        handler = REPLAY_HANDLERS.get(data_source)
        if not handler:
            logger.error(f"Unknown data source: {data_source}")
            return False

        # Call handler (async or sync), passing timestamp if handler accepts it
        sig = inspect.signature(handler)
        kwargs = {}
        if "timestamp" in sig.parameters:
            kwargs["timestamp"] = log_timestamp

        if inspect.iscoroutinefunction(handler):
            success = await handler(data, **kwargs)
        else:
            success = handler(data, **kwargs)

        if success:
            logger.info(f"Successfully replayed data from {log_file}")
        else:
            logger.error(f"Failed to replay data from {log_file}")

        return success

    except Exception as e:
        logger.error(f"Exception replaying log file {log_file}: {e}")
        traceback.print_exc()
        return False


def _parse_log_filename_time(log_file: Path) -> Optional[str]:
    """Extract YYYYMMDD_HHMMSS from log filename."""
    stem = log_file.stem  # e.g. "20260406_145544"
    if len(stem) == 15 and stem[8] == "_":
        return stem
    return None


def _filter_logs_by_time(
    log_files: list[Path], start: Optional[str], stop: Optional[str]
) -> list[Path]:
    """Filter log files by time range using filename timestamps.

    Args:
        log_files: List of log file paths
        start: Start time as HHMM or YYYYMMDD_HHMM (inclusive)
        stop: Stop time as HHMM or YYYYMMDD_HHMM (inclusive)
    """
    if not start and not stop:
        return log_files

    filtered = []
    for log_file in log_files:
        file_time = _parse_log_filename_time(log_file)
        if not file_time:
            continue

        # Support both HHMM (today) and YYYYMMDD_HHMM formats
        if start:
            start_cmp = start.replace(":", "").replace("-", "").replace("_", "")
            if len(start_cmp) <= 4:
                # HHMM - compare only time portion
                file_hhmm = file_time[9:13]
                if file_hhmm < start_cmp:
                    continue
            else:
                if file_time < start_cmp[:15]:
                    continue

        if stop:
            stop_cmp = stop.replace(":", "").replace("-", "").replace("_", "")
            if len(stop_cmp) <= 4:
                file_hhmm = file_time[9:13]
                if file_hhmm > stop_cmp:
                    continue
            else:
                if file_time > stop_cmp[:15]:
                    continue

        filtered.append(log_file)

    return filtered


async def replay_logs(
    data_source: str,
    days: int = 7,
    dry_run: bool = False,
    limit: Optional[int] = None,
    start: Optional[str] = None,
    stop: Optional[str] = None,
) -> tuple[int, int]:
    """
    Replay JSON logs for a data source.

    Args:
        data_source: Data source name
        days: Number of days to look back
        dry_run: If True, don't actually write to InfluxDB
        limit: Maximum number of files to replay
        start: Start time filter (HHMM or YYYYMMDD_HHMM)
        stop: Stop time filter (HHMM or YYYYMMDD_HHMM)

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

    if start or stop:
        log_files = _filter_logs_by_time(log_files, start, stop)
        logger.info(f"After time filter: {len(log_files)} files")

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


def _parse_arguments(print_help: bool = False):
    """Parse and return command line arguments."""
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

  # Replay temperature logs between 14:55 and 15:35 today
  python -m src.tools.replay_json_logs --source temperature --days 1 --start 1455 --stop 1535
        """,
    )
    parser.add_argument("--list", action="store_true", help="List available log files")
    parser.add_argument("--source", help="Data source name to replay")
    parser.add_argument(
        "--days", type=int, default=7, help="Number of days to look back (default: 7)"
    )
    parser.add_argument("--start", help="Start time filter (HHMM or YYYYMMDD_HHMM)")
    parser.add_argument("--stop", help="Stop time filter (HHMM or YYYYMMDD_HHMM)")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be replayed without writing"
    )
    parser.add_argument("--limit", type=int, help="Limit number of files to replay")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    if print_help:
        parser.print_help()
        return None
    return parser.parse_args()


def _handle_list_mode(data_source: Optional[str], days: int) -> int:
    """Handle list mode: display available log files."""
    available_logs = list_available_logs(data_source=data_source, days=days)

    if not available_logs:
        print("No log files found")
        return 0

    for source_name, log_files in available_logs.items():
        print(f"\n{source_name}: {len(log_files)} log files")
        for log_file in log_files[:10]:
            mtime = log_file.stat().st_mtime
            mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  {log_file.name} ({mtime_str})")
        if len(log_files) > 10:
            print(f"  ... and {len(log_files) - 10} more")

    return 0


async def _handle_replay_mode(
    data_source: str,
    days: int,
    dry_run: bool,
    limit: Optional[int],
    start: Optional[str] = None,
    stop: Optional[str] = None,
) -> int:
    """Handle replay mode: replay logs to InfluxDB."""
    success_count, failure_count = await replay_logs(
        data_source=data_source,
        days=days,
        dry_run=dry_run,
        limit=limit,
        start=start,
        stop=stop,
    )
    return 1 if failure_count > 0 else 0


def main():
    """Main entry point for JSON log replay utility."""
    args = _parse_arguments()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    if args.list:
        return _handle_list_mode(args.source, args.days)

    if not args.source:
        print("Error: --source is required (unless using --list)\n")
        _parse_arguments(print_help=True)
        return 1

    # Replay logs
    try:
        return asyncio.run(
            _handle_replay_mode(
                args.source, args.days, args.dry_run, args.limit, args.start, args.stop
            )
        )
    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
