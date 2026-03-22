#!/usr/bin/env python3
"""
Entry point script for 15-minute analytics aggregation.

This script runs the 15-minute aggregation pipeline using the Analytics15MinAggregator class.
Includes gap detection and retry logic to recover missed windows.
"""

import argparse
import datetime
import logging

import pytz

from src.aggregation.analytics_15min import Analytics15MinAggregator
from src.aggregation.gap_detector import find_gaps
from src.common.config import get_config
from src.common.influx_client import InfluxClient

logger = logging.getLogger(__name__)

INTERVAL_MINUTES = 15
MAX_RETRY_WINDOWS = 4


def _fill_gaps(
    aggregator: Analytics15MinAggregator,
    client: InfluxClient,
    window_end: datetime.datetime,
    dry_run: bool,
) -> int:
    """Check for and fill gaps in the last N windows before window_end."""
    const_interval = datetime.timedelta(minutes=INTERVAL_MINUTES)
    const_lookback = const_interval * MAX_RETRY_WINDOWS
    lookback_start = window_end - const_lookback

    bucket = aggregator.config.influxdb_bucket_analytics_15min
    gaps = find_gaps(client, bucket, "analytics", lookback_start, window_end, INTERVAL_MINUTES)

    if not gaps:
        return 0

    logger.info(f"Found {len(gaps)} missing 15-min windows, attempting to fill")
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


def run_aggregation(window_end: datetime.datetime, dry_run: bool = False) -> bool:
    """
    Run 15-minute aggregation for a specific window.

    Args:
        window_end: End timestamp of the 15-min window to aggregate
        dry_run: If True, don't write to InfluxDB

    Returns:
        True if successful, False otherwise
    """
    logger.info("Starting 15-minute analytics aggregation")
    logger.info(
        f"Aggregating window: {window_end - datetime.timedelta(minutes=15)} to {window_end}"
    )

    config = get_config()
    client = InfluxClient(config)
    aggregator = Analytics15MinAggregator(client, config)

    try:
        # Fill any gaps from recent missed windows
        _fill_gaps(aggregator, client, window_end, dry_run)

        # Calculate window start
        window_start = window_end - datetime.timedelta(minutes=INTERVAL_MINUTES)

        # Run aggregation pipeline
        write_to_influx = not dry_run
        metrics = aggregator.aggregate_window(
            window_start, window_end, write_to_influx=write_to_influx
        )

        if metrics is None:
            logger.warning("No data available for 15-min window - skipping")
            return True

        if dry_run:
            logger.info(
                f"DRY RUN: Would write {len(metrics)} fields to analytics_15min at {window_end}"
            )
            logger.debug(f"Fields: {metrics}")

        logger.info("15-minute analytics aggregation completed successfully")
        return True
    finally:
        client.close()


def main():
    """Main entry point for 15-minute analytics aggregation."""
    parser = argparse.ArgumentParser(description="15-minute analytics aggregator")
    parser.add_argument(
        "--window-end",
        type=str,
        help="End timestamp of window (ISO format with timezone, e.g. 2026-01-08T10:15:00+00:00)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Don't write to InfluxDB")
    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Determine window end time
    if args.window_end:
        window_end = datetime.datetime.fromisoformat(args.window_end)
    else:
        # Default: process the previous completed 15-min window
        now = datetime.datetime.now(pytz.UTC)
        # Round down to previous 15-min mark
        minutes = (now.minute // 15) * 15
        window_end = now.replace(minute=minutes, second=0, microsecond=0)

    success = run_aggregation(window_end, dry_run=args.dry_run)
    return 0 if success else 1


if __name__ == "__main__":
    exit(main())
