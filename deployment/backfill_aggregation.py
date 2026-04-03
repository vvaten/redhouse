#!/usr/bin/env python3
"""
Backfill aggregation pipelines over a historical time range.

Runs the 5-min, 15-min, and 1-hour aggregation pipelines in order for each
window in the given range. Use this to recover from gaps caused by service
downtime or bugs.

Order: emeters_5min first, then analytics_15min, then analytics_1hour,
because each tier reads from the previous one.

Usage:
    python -u backfill_aggregation.py --days 7
    python -u backfill_aggregation.py --start 2026-02-15 --end 2026-02-22
    python -u backfill_aggregation.py --days 1 --dry-run
    python -u backfill_aggregation.py --days 1 --skip-5min
"""

import argparse
import datetime
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.aggregation.analytics_1hour import Analytics1HourAggregator
from src.aggregation.analytics_15min import Analytics15MinAggregator
from src.aggregation.emeters_5min import Emeters5MinAggregator
from src.common.config import get_config
from src.common.influx_client import InfluxClient

UTC = pytz.UTC


def find_bucket_retention(client: InfluxClient, bucket_name: str) -> int:
    """Query InfluxDB for a bucket's retention period in seconds.

    Returns retention in seconds, or 0 for infinite retention.
    """
    try:
        buckets_api = client.client.buckets_api()
        bucket = buckets_api.find_bucket_by_name(bucket_name)
        if bucket and bucket.retention_rules:
            for rule in bucket.retention_rules:
                if rule.every_seconds and rule.every_seconds > 0:
                    return rule.every_seconds
    except Exception as e:
        print(f"  WARNING: Could not query retention for {bucket_name}: {e}")
    return 0


def find_data_range(
    client: InfluxClient,
    bucket: str,
    measurement: str,
) -> tuple:
    """Query InfluxDB for the first and last timestamp in a bucket.

    Returns (first_time, last_time) or (None, None) if bucket is empty.
    """
    query = f"""
import "date"

first = from(bucket: "{bucket}")
  |> range(start: 0)
  |> filter(fn: (r) => r["_measurement"] == "{measurement}")
  |> first()
  |> keep(columns: ["_time"])

last = from(bucket: "{bucket}")
  |> range(start: 0)
  |> filter(fn: (r) => r["_measurement"] == "{measurement}")
  |> last()
  |> keep(columns: ["_time"])

union(tables: [first, last])
  |> sort(columns: ["_time"])
"""
    try:
        tables = client.query_with_retry(query)
        times = []
        for table in tables:
            for record in table.records:
                times.append(record.get_time())
        if len(times) >= 2:
            return min(times), max(times)
        if len(times) == 1:
            return times[0], times[0]
    except Exception as e:
        print(f"  WARNING: Could not detect data range: {e}")
    return None, None


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Backfill aggregation pipelines over a historical time range",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument(
        "--days",
        type=int,
        help="Number of days to backfill (from N days ago until now)",
    )
    date_group.add_argument(
        "--start",
        type=str,
        help="Start date (YYYY-MM-DD, requires --end)",
    )

    parser.add_argument(
        "--end",
        type=str,
        help="End date inclusive (YYYY-MM-DD, requires --start)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without writing to InfluxDB",
    )
    parser.add_argument(
        "--skip-5min",
        action="store_true",
        help="Skip 5-min aggregation (e.g. already done, only redo analytics)",
    )
    parser.add_argument(
        "--skip-15min",
        action="store_true",
        help="Skip 15-min analytics aggregation",
    )
    parser.add_argument(
        "--skip-1hour",
        action="store_true",
        help="Skip 1-hour analytics aggregation",
    )

    return parser.parse_args()


def _round_down(dt: datetime.datetime, interval_minutes: int) -> datetime.datetime:
    """Round datetime down to the nearest interval boundary."""
    const_total_minutes = (dt.hour * 60 + dt.minute) // interval_minutes * interval_minutes
    return dt.replace(
        hour=const_total_minutes // 60,
        minute=const_total_minutes % 60,
        second=0,
        microsecond=0,
    )


def iter_windows(
    start: datetime.datetime,
    end: datetime.datetime,
    interval_minutes: int,
) -> Iterator[datetime.datetime]:
    """Yield window_end timestamps covering [start, end]."""
    const_step = datetime.timedelta(minutes=interval_minutes)
    current = _round_down(start, interval_minutes) + const_step
    while current <= end:
        yield current
        current += const_step


def run_tier(
    label: str,
    windows: list,
    aggregator,
    write_to_influx: bool,
) -> tuple:
    """Run aggregation for all windows in a tier. Returns (succeeded, skipped)."""
    print(f"\n--- {label} ({len(windows)} windows) ---")
    succeeded = 0
    skipped = 0

    for window_end in windows:
        window_start = window_end - datetime.timedelta(seconds=aggregator.INTERVAL_SECONDS)
        result = aggregator.aggregate_window(
            window_start, window_end, write_to_influx=write_to_influx
        )
        if result is not None:
            succeeded += 1
        else:
            skipped += 1
            print(f"  SKIP {window_end.isoformat()}")

        done = succeeded + skipped
        print(
            f"  [{done}/{len(windows)}] {window_start.strftime('%Y-%m-%d %H:%M')} - "
            f"{window_end.strftime('%H:%M')}",
            end="\r",
        )

    print(f"  Done: {succeeded} written, {skipped} skipped/no-data     ")
    return succeeded, skipped


def _resolve_time_range(
    args: argparse.Namespace,
) -> tuple:
    """Parse args into (start_time, end_time) or return (None, None) on error."""
    if args.start and not args.end:
        print("ERROR: --start requires --end", file=sys.stderr)
        return None, None
    if args.end and not args.start:
        print("ERROR: --end requires --start", file=sys.stderr)
        return None, None

    if args.days:
        end_time = datetime.datetime.now(UTC)
        start_time = end_time - datetime.timedelta(days=args.days)
        print(f"Backfilling last {args.days} days")
        return start_time, end_time

    try:
        start_time = UTC.localize(datetime.datetime.strptime(args.start, "%Y-%m-%d"))
        end_time = UTC.localize(
            datetime.datetime.strptime(args.end, "%Y-%m-%d")
        ) + datetime.timedelta(days=1)
        print(f"Backfilling {args.start} to {args.end}")
        return start_time, end_time
    except ValueError as e:
        print(f"ERROR: Invalid date format: {e}", file=sys.stderr)
        return None, None


def _suppress_aggregator_logging() -> None:
    """Suppress INFO noise from aggregator modules -- progress is shown on stdout.

    Aggregator modules call setup_logger() at import time, which sets INFO level
    directly on each child logger with its own handlers, so setting the parent
    "src" logger level has no effect. We must iterate all registered loggers.
    """
    for _name, _lgr in logging.Logger.manager.loggerDict.items():
        if _name.startswith("src.") and isinstance(_lgr, logging.Logger):
            _lgr.setLevel(logging.WARNING)
            for _handler in _lgr.handlers:
                _handler.setLevel(logging.WARNING)


def _clamp_to_data_range(
    client: InfluxClient,
    config,
    start_time: datetime.datetime,
    end_time: datetime.datetime,
    skip_5min: bool,
) -> tuple:
    """Detect actual data range in the source bucket and clamp the requested range.

    Also checks target bucket retention policies so we don't attempt writes
    that InfluxDB will reject as "beyond retention policy".

    Returns (clamped_start, clamped_end) or (None, None) if no usable range.
    """
    if not skip_5min:
        source_bucket = config.influxdb_bucket_shelly_em3_raw
        source_measurement = "shelly_em3"
    else:
        source_bucket = config.influxdb_bucket_emeters_5min
        source_measurement = "energy"

    print(f"Detecting data range in {source_bucket}...")
    data_first, data_last = find_data_range(client, source_bucket, source_measurement)

    if data_first is None:
        print(f"No data found in {source_bucket} -- nothing to backfill.")
        return None, None

    clamped_start = max(start_time, data_first)
    clamped_end = min(end_time, data_last)

    # Check target bucket retention policies and clamp start forward
    now = datetime.datetime.now(UTC)
    target_buckets = []
    if not skip_5min:
        target_buckets.append(config.influxdb_bucket_emeters_5min)
    target_buckets.append(config.influxdb_bucket_analytics_15min)
    target_buckets.append(config.influxdb_bucket_analytics_1hour)

    for bucket_name in target_buckets:
        retention_s = find_bucket_retention(client, bucket_name)
        if retention_s > 0:
            # Add 1h buffer to avoid edge-case rejections
            earliest_writable = now - datetime.timedelta(seconds=retention_s - 3600)
            if earliest_writable > clamped_start:
                print(
                    f"  {bucket_name}: {retention_s // 86400}d retention, "
                    f"earliest writable ~{earliest_writable.strftime('%Y-%m-%d %H:%M')}"
                )
                clamped_start = max(clamped_start, earliest_writable)

    if clamped_start >= clamped_end:
        print("No writable range after applying retention constraints.")
        return None, None

    if clamped_start != start_time or clamped_end != end_time:
        print(f"Data available: {data_first.isoformat()} -> {data_last.isoformat()}")
        print(f"Clamped range:  {clamped_start.isoformat()} -> {clamped_end.isoformat()}")
    else:
        print("Data covers full requested range.")

    return clamped_start, clamped_end


def main() -> int:
    """Main entry point."""
    args = parse_args()

    start_time, end_time = _resolve_time_range(args)
    if start_time is None:
        return 1

    _suppress_aggregator_logging()

    print(f"Requested range: {start_time.isoformat()} -> {end_time.isoformat()}")
    if args.dry_run:
        print("DRY-RUN: no data will be written")

    config = get_config()
    client = InfluxClient(config)

    try:
        write = not args.dry_run

        start_time, end_time = _clamp_to_data_range(
            client, config, start_time, end_time, args.skip_5min
        )
        if start_time is None:
            return 0

        if not args.skip_5min:
            windows = list(iter_windows(start_time, end_time, 5))
            run_tier(
                "5-minute emeters aggregation",
                windows,
                Emeters5MinAggregator(client, config),
                write,
            )

        if not args.skip_15min:
            windows = list(iter_windows(start_time, end_time, 15))
            run_tier(
                "15-minute analytics aggregation",
                windows,
                Analytics15MinAggregator(client, config),
                write,
            )

        if not args.skip_1hour:
            windows = list(iter_windows(start_time, end_time, 60))
            run_tier(
                "1-hour analytics aggregation",
                windows,
                Analytics1HourAggregator(client, config),
                write,
            )

        print("\nBackfill complete.")
        return 0

    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1

    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
