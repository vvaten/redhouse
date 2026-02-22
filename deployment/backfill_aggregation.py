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


def main() -> int:
    """Main entry point."""
    args = parse_args()

    start_time, end_time = _resolve_time_range(args)
    if start_time is None:
        return 1

    # Suppress INFO noise from aggregator modules - progress is shown on stdout.
    # Aggregator modules call setup_logger() at import time, which sets INFO level
    # directly on each child logger with its own handlers, so setting the parent
    # "src" logger level has no effect. We must iterate all registered loggers.
    for _name, _lgr in logging.Logger.manager.loggerDict.items():
        if _name.startswith("src.") and isinstance(_lgr, logging.Logger):
            _lgr.setLevel(logging.WARNING)
            for _handler in _lgr.handlers:
                _handler.setLevel(logging.WARNING)

    print(f"Range: {start_time.isoformat()} -> {end_time.isoformat()}")
    if args.dry_run:
        print("DRY-RUN: no data will be written")

    config = get_config()
    client = InfluxClient(config)

    try:
        write = not args.dry_run

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
