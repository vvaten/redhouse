#!/usr/bin/env python3
"""
Copy data between two InfluxDB buckets, one day at a time.

Built for the one-time migration of CheckWatt history from the wibatemp
bucket checkwatt_full_data to the redhouse bucket checkwatt. Works for any
bucket pair. Points keep their measurement, tags, fields and timestamps, so
a re-run over the same range overwrites identical points and is safe.

Writes require --confirm. The destination bucket must exist unless
--create-dest is given, which creates it with infinite retention.

Usage:
    python -u deployment/copy_bucket.py --source checkwatt_full_data \
        --dest checkwatt --days 1 --dry-run
    python -u deployment/copy_bucket.py --source checkwatt_full_data \
        --dest checkwatt --start 2025-02-27 --end 2026-09-09 \
        --create-dest --confirm
"""

import argparse
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import copy_production_to_staging  # noqa: E402
from copy_production_to_staging import copy_bucket_data  # noqa: E402
from create_aggregation_buckets import create_bucket_if_not_exists  # noqa: E402
from influxdb_client import InfluxDBClient  # noqa: E402

from src.common.config import get_config  # noqa: E402

DATE_FORMAT = "%Y-%m-%d"
CLIENT_TIMEOUT_MS = 120_000
MAX_ATTEMPTS = 3
# Lower this if InfluxDB reports write timeouts on large batches
DEFAULT_BATCH_SIZE = 5000


def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Copy data between two InfluxDB buckets, one day at a time",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--source", required=True, help="Source bucket name")
    parser.add_argument("--dest", required=True, help="Destination bucket name")

    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument("--days", type=int, help="Copy the last N days")
    date_group.add_argument("--start", help="Start date YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", help="End date YYYY-MM-DD (inclusive, needs --start)")

    parser.add_argument("--dry-run", action="store_true", help="Count records, write nothing")
    parser.add_argument("--confirm", action="store_true", help="Required to write data")
    parser.add_argument(
        "--create-dest",
        action="store_true",
        help="Create the destination bucket with infinite retention if missing",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Points per write request (default {DEFAULT_BATCH_SIZE})",
    )
    return parser.parse_args(argv)


def resolve_time_range(args: argparse.Namespace) -> "tuple[datetime, datetime]":
    """Turn --days or --start/--end into a half-open UTC range."""
    if args.days is not None:
        if args.end:
            raise ValueError("--end cannot be combined with --days")
        end_time = datetime.utcnow()
        return end_time - timedelta(days=args.days), end_time

    if not args.end:
        raise ValueError("--start requires --end")
    start_time = datetime.strptime(args.start, DATE_FORMAT)
    end_time = datetime.strptime(args.end, DATE_FORMAT) + timedelta(days=1)
    if end_time <= start_time:
        raise ValueError("--end must not be before --start")
    return start_time, end_time


def iter_day_windows(
    start_time: datetime, end_time: datetime
) -> Iterator["tuple[datetime, datetime]"]:
    """Yield consecutive [window_start, window_end) pairs of at most one day."""
    window_start = start_time
    while window_start < end_time:
        window_end = min(window_start + timedelta(days=1), end_time)
        yield window_start, window_end
        window_start = window_end


def ensure_destination(client: InfluxDBClient, dest: str, create: bool) -> bool:
    """Check the destination bucket exists, create it when asked."""
    buckets_api = client.buckets_api()
    if buckets_api.find_bucket_by_name(dest) is not None:
        return True
    if not create:
        print(f"ERROR: destination bucket '{dest}' does not exist (use --create-dest)")
        return False
    org_id = client.organizations_api().find_organizations(org=client.org)[0].id
    return create_bucket_if_not_exists(client, org_id, dest, retention_seconds=0)


def copy_range(
    client: InfluxDBClient,
    source: str,
    dest: str,
    start_time: datetime,
    end_time: datetime,
    dry_run: bool,
) -> int:
    """Copy every day window in the range and return the record total."""
    total = 0
    windows = list(iter_day_windows(start_time, end_time))
    for index, (window_start, window_end) in enumerate(windows, start=1):
        print(f"\n[{index}/{len(windows)}]", end="")
        total += _copy_window_with_retry(client, source, dest, window_start, window_end, dry_run)
    return total


def _copy_window_with_retry(
    client: InfluxDBClient,
    source: str,
    dest: str,
    window_start: datetime,
    window_end: datetime,
    dry_run: bool,
) -> int:
    """Copy one window, retrying on InfluxDB errors such as read timeouts."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return copy_bucket_data(client, source, dest, window_start, window_end, dry_run)
        except Exception as e:
            if attempt == MAX_ATTEMPTS:
                raise
            print(f"  Attempt {attempt} failed ({e}), retrying...")
    return 0


def main() -> int:
    """Main entry point."""
    args = parse_args()

    try:
        start_time, end_time = resolve_time_range(args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.source == args.dest:
        print("ERROR: --source and --dest must differ", file=sys.stderr)
        return 1

    if not args.dry_run and not args.confirm:
        print("ERROR: writes need --confirm (or use --dry-run)", file=sys.stderr)
        return 1

    copy_production_to_staging.WRITE_BATCH_SIZE = args.batch_size
    mode = "DRY-RUN" if args.dry_run else "WRITE"
    print(f"{mode}: {args.source} -> {args.dest}")
    print(f"Range: {start_time} to {end_time} (UTC)")
    print("=" * 60)

    config = get_config()
    client = InfluxDBClient(
        url=config.influxdb_url,
        token=config.influxdb_token,
        org=config.influxdb_org,
        timeout=CLIENT_TIMEOUT_MS,
    )

    try:
        if not args.dry_run and not ensure_destination(client, args.dest, args.create_dest):
            return 1
        total = copy_range(client, args.source, args.dest, start_time, end_time, args.dry_run)
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1
    finally:
        client.close()

    verb = "would be copied" if args.dry_run else "copied"
    print("\n" + "=" * 60)
    print(f"Total records {verb}: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
