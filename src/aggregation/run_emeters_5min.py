#!/usr/bin/env python3
"""
Entry point script for 5-minute energy meter aggregation.

This script runs the 5-minute aggregation pipeline using the Emeters5MinAggregator class.
Includes gap detection and retry logic to recover missed windows.
"""

import argparse
import datetime
from typing import Optional

import pytz

from src.aggregation.emeters_5min import Emeters5MinAggregator
from src.aggregation.gap_detector import find_gaps
from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger

logger = setup_logger(__name__, "emeters_5min.log")

INTERVAL_MINUTES = 5
MAX_RETRY_WINDOWS = 6


def _fill_gaps(
    aggregator: Emeters5MinAggregator,
    client: InfluxClient,
    window_end: datetime.datetime,
    dry_run: bool,
) -> int:
    """Check for and fill gaps in the last N windows before window_end."""
    const_interval = datetime.timedelta(minutes=INTERVAL_MINUTES)
    const_lookback = const_interval * MAX_RETRY_WINDOWS
    lookback_start = window_end - const_lookback

    bucket = aggregator.config.influxdb_bucket_emeters_5min
    gaps = find_gaps(client, bucket, "energy", lookback_start, window_end, INTERVAL_MINUTES)

    if not gaps:
        return 0

    logger.info(f"Found {len(gaps)} missing windows, attempting to fill")
    filled = 0
    for gap_end in gaps:
        gap_start = gap_end - const_interval
        logger.info(f"Retrying gap: {gap_start} - {gap_end}")
        result = aggregator.aggregate_window(gap_start, gap_end, write_to_influx=not dry_run)
        if result is not None:
            filled += 1
            logger.info(f"Filled gap at {gap_start}")
        else:
            logger.warning(f"Could not fill gap at {gap_start} (no source data?)")

    logger.info(f"Gap fill complete: {filled}/{len(gaps)} windows recovered")
    return filled


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

    window_start = window_end - datetime.timedelta(minutes=INTERVAL_MINUTES)

    logger.info(f"Aggregating window: {window_start} to {window_end}")

    # Initialize aggregator
    config = get_config()
    client = InfluxClient(config)
    aggregator = Emeters5MinAggregator(client, config)

    try:
        # Fill any gaps from recent missed windows
        _fill_gaps(aggregator, client, window_end, dry_run)

        # Run aggregation pipeline for current window
        write_to_influx = not dry_run
        metrics = aggregator.aggregate_window(
            window_start, window_end, write_to_influx=write_to_influx
        )

        if metrics is not None:
            logger.info("5-minute aggregation completed successfully")
            if dry_run:
                logger.info(f"DRY RUN: Would have written {len(metrics)} fields")
                logger.debug(f"Fields: {metrics}")
            return 0
        else:
            logger.error("5-minute aggregation failed")
            return 1
    finally:
        client.close()


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
