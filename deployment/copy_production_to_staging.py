#!/usr/bin/env python3
"""
Copy historical data from production buckets to staging buckets.

This allows testing the heating program generator with real production data
before switching to staging mode.

Usage:
    python -u copy_production_to_staging.py --days 30
    python -u copy_production_to_staging.py --start 2024-10-01 --end 2024-10-31
    python -u copy_production_to_staging.py --days 7 --dry-run

Requirements:
    pip install influxdb-client
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from influxdb_client import InfluxDBClient

from src.common.config import get_config

# Bucket mappings: production -> staging
BUCKET_MAPPINGS = {
    "temperatures": "temperatures_staging",
    "weather": "weather_staging",
    "spotprice": "spotprice_staging",
    "emeters": "emeters_staging",
    "checkwatt_full_data": "checkwatt_staging",
    "shelly_em3_emeters_raw": "shelly_em3_emeters_raw_staging",
    "load_control": "load_control_staging",
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

    return parser.parse_args()


def format_timestamp(dt):
    """Format datetime for InfluxDB query."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def copy_bucket_data(client, source_bucket, dest_bucket, start_time, end_time, dry_run=False):
    """
    Copy data from source bucket to destination bucket.

    Args:
        client: InfluxDB client
        source_bucket: Source bucket name
        dest_bucket: Destination bucket name
        start_time: Start datetime
        end_time: End datetime
        dry_run: If True, only show what would be copied

    Returns:
        Number of records copied
    """
    print(f"\nCopying: {source_bucket} -> {dest_bucket}")
    print(f"  Time range: {start_time} to {end_time}")

    # Query data from source bucket
    query_api = client.query_api()
    query = f"""
    from(bucket: "{source_bucket}")
      |> range(start: {format_timestamp(start_time)}, stop: {format_timestamp(end_time)})
    """

    if dry_run:
        # Just count records
        count_query = query + "|> count()"
        result = query_api.query(count_query)
        total_records = 0
        for table in result:
            for record in table.records:
                total_records += record.get_value()
        print(f"  Would copy: {total_records} records (DRY-RUN)")
        return total_records

    # Get all data
    result = query_api.query(query)

    # Write to destination bucket
    write_api = client.write_api()
    records_copied = 0

    for table in result:
        for record in table.records:
            # Convert back to line protocol format
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

            write_api.write(bucket=dest_bucket, record=point_dict)
            records_copied += 1

            if records_copied % 1000 == 0:
                print(f"  Copied: {records_copied} records...", end="\r")

    write_api.close()
    print(f"  Copied: {records_copied} records (DONE)     ")
    return records_copied


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

    # Load config
    config = get_config()

    # Connect to InfluxDB
    print(f"\nConnecting to InfluxDB: {config.influxdb_url}")
    client = InfluxDBClient(
        url=config.influxdb_url,
        token=config.influxdb_token,
        org=config.influxdb_org,
    )

    try:
        # Copy each bucket
        total_records = 0
        for source_bucket in args.buckets:
            dest_bucket = BUCKET_MAPPINGS[source_bucket]
            records = copy_bucket_data(
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
