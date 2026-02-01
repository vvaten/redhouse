#!/usr/bin/env python3
"""
Entry point script for 5-minute energy meter aggregation.

This script runs the 5-minute aggregation pipeline using the Emeters5MinAggregator class.
"""

import argparse
import datetime
from typing import Optional

import pytz

from src.aggregation.emeters_5min import Emeters5MinAggregator
from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger

logger = setup_logger(__name__, "emeters_5min.log")


def aggregate_5min(window_end: Optional[datetime.datetime] = None, dry_run: bool = False) -> int:
    """
    Main aggregation function for 5-minute windows.

    Args:
        window_end: End time of window (default: current time rounded to 5-min)
        dry_run: If True, don't write to database

    Returns:
        0 on success, 1 on failure
    """
    logger.info("Starting 5-minute aggregation")

    # Determine time window
    if window_end is None:
        now = datetime.datetime.now(pytz.UTC)
        # Round down to last 5-minute boundary
        minute = (now.minute // 5) * 5
        window_end = now.replace(minute=minute, second=0, microsecond=0)

    window_start = window_end - datetime.timedelta(minutes=5)

    logger.info(f"Aggregating window: {window_start} to {window_end}")

    # Initialize aggregator
    config = get_config()
    client = InfluxClient(config)
    aggregator = Emeters5MinAggregator(client, config)

    # Run aggregation pipeline
    write_to_influx = not dry_run
    metrics = aggregator.aggregate_window(window_start, window_end, write_to_influx=write_to_influx)

    client.close()

    if metrics is not None:
        logger.info("5-minute aggregation completed successfully")
        if dry_run:
            logger.info(f"DRY RUN: Would have written {len(metrics)} fields")
            logger.debug(f"Fields: {metrics}")
        return 0
    else:
        logger.error("5-minute aggregation failed")
        return 1


def main():
    """Main entry point for 5-minute aggregation."""
    parser = argparse.ArgumentParser(description="Aggregate 5-minute energy meter data")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write to database, just log what would be done",
    )
    parser.add_argument(
        "--window-end",
        type=str,
        help="End time of window in ISO format (default: current time rounded to 5-min)",
    )

    args = parser.parse_args()

    window_end = None
    if args.window_end:
        window_end = datetime.datetime.fromisoformat(args.window_end)
        if window_end.tzinfo is None:
            window_end = window_end.replace(tzinfo=pytz.UTC)

    exit_code = aggregate_5min(window_end=window_end, dry_run=args.dry_run)
    return exit_code


if __name__ == "__main__":
    import sys

    sys.exit(main())
