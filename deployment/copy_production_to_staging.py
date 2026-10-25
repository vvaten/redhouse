#!/usr/bin/env python3
"""
Fill gaps in the staging buckets with production data. Only windows where
staging has no point are copied, so nothing staging wrote is overwritten.

This allows testing the heating program generator with real production data
before switching to staging mode.

Usage:
    python -u copy_production_to_staging.py --days 30
    python -u copy_production_to_staging.py --start 2024-10-01 --end 2024-10-31
    python -u copy_production_to_staging.py --days 7 --dry-run
    python -u copy_production_to_staging.py --days 2 --overwrite

Requirements:
    pip install influxdb-client
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from influxdb_client import InfluxDBClient

from src.common.config import get_config

WRITE_BATCH_SIZE = 5000
QUERY_TIMEOUT_MS = 60_000

# Bucket mappings: production -> staging
BUCKET_MAPPINGS = {
    # "temperatures": "temperatures_staging",  # Temperatures are read from production bucket - read only
    "weather": "weather_staging",
    "spotprice": "spotprice_staging",
    "emeters": "emeters_staging",
    "checkwatt": "checkwatt_staging",
    "shelly_em3_emeters_raw": "shelly_em3_emeters_raw_staging",
    "load_control": "load_control_staging",
    "windpower": "windpower_staging",
}

# Minutes per window; a window where staging has no point of a measurement
# is a gap. load_control is not here: staging plans and runs its own
# commands, so it is copied only with --overwrite.
GAP_WINDOW_MINUTES = {
    "weather": 60,
    "spotprice": 60,
    "emeters": 15,
    "checkwatt": 15,
    "shelly_em3_emeters_raw": 15,
    "windpower": 60,
}


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Copy production data to staging buckets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument(
        "--days",
        type=int,
        help="Number of days to copy (from N days ago until now)",
    )
    date_group.add_argument(
        "--start",
        type=str,
        help="Start date (YYYY-MM-DD, requires --end)",
    )

    parser.add_argument(
        "--end",
        type=str,
        help="End date (YYYY-MM-DD, requires --start)",
    )

    parser.add_argument(
        "--buckets",
        type=str,
        nargs="+",
        choices=list(BUCKET_MAPPINGS.keys()),
        default=list(BUCKET_MAPPINGS.keys()),
        help="Which buckets to copy (default: all)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be copied without actually copying",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Copy the whole range over what staging has, not only its gaps",
    )

    return parser.parse_args()


def format_timestamp(dt):
    """Format datetime for InfluxDB query."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_range_query(source_bucket, start_time, end_time, measurement=None):
    """Build InfluxDB range query for data extraction, of one measurement if given."""
    query = f"""
    from(bucket: "{source_bucket}")
      |> range(start: {format_timestamp(start_time)}, stop: {format_timestamp(end_time)})
    """
    if measurement is not None:
        query += f'  |> filter(fn: (r) => r._measurement == "{measurement}")\n'
    return query


def _count_records_dry_run(query_api, query):
    """Count records for dry-run mode."""
    count_query = query + "|> count()"
    result = query_api.query(count_query)
    total_records = 0
    for table in result:
        for record in table.records:
            total_records += record.get_value()
    return total_records


def _convert_record_to_point(record):
    """Convert InfluxDB query record to point dict for writing."""
    point_dict = {
        "measurement": record.get_measurement(),
        "tags": record.values.get("tags", {}),
        "fields": {record.get_field(): record.get_value()},
        "time": record.get_time(),
    }

    # Copy all tags
    for key, value in record.values.items():
        if key not in [
            "_measurement",
            "_field",
            "_value",
            "_time",
            "_start",
            "_stop",
            "result",
            "table",
        ]:
            point_dict["tags"][key] = value

    return point_dict


def copy_bucket_data(
    client, source_bucket, dest_bucket, start_time, end_time, dry_run=False, measurement=None
):
    """
    Copy data from source bucket to destination bucket.

    Args:
        client: InfluxDB client
        source_bucket: Source bucket name
        dest_bucket: Destination bucket name
        start_time: Start datetime
        end_time: End datetime
        dry_run: If True, only show what would be copied
        measurement: Copy only this measurement, or all if None

    Returns:
        Number of records copied
    """
    print(f"\nCopying: {source_bucket} -> {dest_bucket}")
    print(f"  Time range: {start_time} to {end_time}")

    query_api = client.query_api()
    query = _build_range_query(source_bucket, start_time, end_time, measurement)

    if dry_run:
        total_records = _count_records_dry_run(query_api, query)
        print(f"  Would copy: {total_records} records (DRY-RUN)")
        return total_records

    # Get all data
    result = query_api.query(query)

    # Write to destination bucket in batches
    write_api = client.write_api()
    records_copied = 0
    batch = []

    for table in result:
        for record in table.records:
            batch.append(_convert_record_to_point(record))
            if len(batch) >= WRITE_BATCH_SIZE:
                write_api.write(bucket=dest_bucket, record=batch)
                records_copied += len(batch)
                batch = []
                print(f"  Copied: {records_copied} records...", end="\r")

    if batch:
        write_api.write(bucket=dest_bucket, record=batch)
        records_copied += len(batch)

    write_api.close()
    print(f"  Copied: {records_copied} records (DONE)     ")
    return records_copied


def _window_counts_query(bucket, start_time, end_time, minutes):
    """Windows of each series that hold a point. A count right after the range
    runs in the storage engine, which keeps a long range fast."""
    return f"""
    from(bucket: "{bucket}")
      |> range(start: {format_timestamp(start_time)}, stop: {format_timestamp(end_time)})
      |> aggregateWindow(every: {minutes}m, fn: count, timeSrc: "_start", createEmpty: false)
      |> keep(columns: ["_time", "_measurement"])
    """


def _window_start(time, minutes):
    """The start of the window on the epoch grid; Flux clips the first window
    to the range start, so its _start is not on the grid."""
    step = minutes * 60
    return datetime.fromtimestamp(int(time.timestamp()) // step * step, timezone.utc)


def _occupied_windows(query_api, bucket, start_time, end_time, minutes):
    """(measurement, window start) of each window that holds data."""
    tables = query_api.query(_window_counts_query(bucket, start_time, end_time, minutes))
    return {
        (record.get_measurement(), _window_start(record.get_time(), minutes))
        for table in tables
        for record in table.records
    }


def _gap_ranges(source_windows, dest_windows, minutes, start_time, end_time):
    """Merged (measurement, start, end) ranges of the windows only the source
    has data in, clipped to the copy range. The last two windows are left
    out, as staging writes some data a few minutes late."""
    step = timedelta(minutes=minutes)
    ranges: list = []
    for measurement, window in sorted(source_windows - dest_windows):
        if window + 2 * step > end_time:
            continue
        begin, end = max(window, start_time), window + step
        if ranges and ranges[-1][0] == measurement and ranges[-1][2] >= begin:
            ranges[-1] = (measurement, ranges[-1][1], end)
        else:
            ranges.append((measurement, begin, end))
    return ranges


def fill_bucket_gaps(client, source_bucket, dest_bucket, start_time, end_time, dry_run=False):
    """Copy only the windows where the destination has no data of a
    measurement, so no point the destination wrote itself is overwritten.
    Returns records copied."""
    if source_bucket not in GAP_WINDOW_MINUTES:
        print(f"\n{source_bucket}: copied only with --overwrite")
        return 0
    start_time = start_time.replace(tzinfo=timezone.utc)
    end_time = end_time.replace(tzinfo=timezone.utc)
    minutes = GAP_WINDOW_MINUTES[source_bucket]
    query_api = client.query_api()
    source = _occupied_windows(query_api, source_bucket, start_time, end_time, minutes)
    dest = _occupied_windows(query_api, dest_bucket, start_time, end_time, minutes)
    ranges = _gap_ranges(source, dest, minutes, start_time, end_time)
    print(f"\n{source_bucket}: {len(ranges)} gaps in {len(source)} windows of {minutes} min")
    return sum(
        copy_bucket_data(client, source_bucket, dest_bucket, begin, end, dry_run, measurement)
        for measurement, begin, end in ranges
    )


def _connect(config):
    """An InfluxDB client with a one-minute timeout."""
    print(f"\nConnecting to InfluxDB: {config.influxdb_url}")
    return InfluxDBClient(
        url=config.influxdb_url,
        token=config.influxdb_token,
        org=config.influxdb_org,
        timeout=QUERY_TIMEOUT_MS,
    )


def main():
    """Main entry point."""
    args = parse_args()

    # Validate date arguments
    if args.start and not args.end:
        print("ERROR: --start requires --end", file=sys.stderr)
        return 1
    if args.end and not args.start:
        print("ERROR: --end requires --start", file=sys.stderr)
        return 1

    # Calculate time range
    if args.days:
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=args.days)
        print(f"Copying last {args.days} days of data")
    else:
        try:
            start_time = datetime.strptime(args.start, "%Y-%m-%d")
            end_time = datetime.strptime(args.end, "%Y-%m-%d") + timedelta(days=1)
            print(f"Copying data from {args.start} to {args.end}")
        except ValueError as e:
            print(f"ERROR: Invalid date format: {e}", file=sys.stderr)
            return 1

    if args.dry_run:
        print("DRY-RUN MODE: No data will be copied")

    print("=" * 60)

    client = _connect(get_config())
    copy_range = copy_bucket_data if args.overwrite else fill_bucket_gaps
    print("Overwriting staging with the whole range" if args.overwrite else "Filling gaps only")

    try:
        # Copy each bucket
        total_records = 0
        for source_bucket in args.buckets:
            dest_bucket = BUCKET_MAPPINGS[source_bucket]
            records = copy_range(
                client, source_bucket, dest_bucket, start_time, end_time, args.dry_run
            )
            total_records += records

        print("\n" + "=" * 60)
        print(
            f"Total records {'that would be copied' if args.dry_run else 'copied'}: {total_records}"
        )
        print("=" * 60)

        if args.dry_run:
            print("\nRun without --dry-run to actually copy the data")
        else:
            print("\nStaging buckets populated with production data!")
            print("You can now test program generation with real data.")

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
