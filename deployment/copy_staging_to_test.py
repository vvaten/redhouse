#!/usr/bin/env python3
"""
Copy data from staging buckets to test buckets for local development.

Usage:
    python deployment/copy_staging_to_test.py --minutes 30
    python deployment/copy_staging_to_test.py --hours 2
    python deployment/copy_staging_to_test.py --minutes 30 --dry-run
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from influxdb_client import InfluxDBClient, Point
from src.common.config import get_config

# Bucket mappings: staging -> test
BUCKET_MAPPINGS = {
    "checkwatt_staging": "checkwatt_full_data_test",
    "shelly_em3_emeters_raw_staging": "shelly_em3_emeters_raw_test",
}


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Copy staging data to test buckets for local development"
    )

    time_group = parser.add_mutually_exclusive_group(required=True)
    time_group.add_argument("--minutes", type=int, help="Minutes of recent data to copy")
    time_group.add_argument("--hours", type=int, help="Hours of recent data to copy")

    parser.add_argument(
        "--buckets",
        type=str,
        nargs="+",
        choices=list(BUCKET_MAPPINGS.keys()),
        default=list(BUCKET_MAPPINGS.keys()),
        help="Which buckets to copy (default: all)",
    )

    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be copied without copying"
    )

    return parser.parse_args()


def copy_bucket_data(client, source_bucket, dest_bucket, start_time, end_time, dry_run=False):
    """
    Copy data from source bucket to destination bucket.

    Returns:
        Number of records copied
    """
    print(f"\nCopying: {source_bucket} -> {dest_bucket}")
    print(f"  Time range: {start_time} to {end_time}")

    # Query data from source bucket
    query_api = client.query_api()
    # Format timestamps for Flux (RFC3339)
    start_str = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_str = end_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    query = f'''
from(bucket: "{source_bucket}")
  |> range(start: {start_str}, stop: {end_str})
'''

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
    config = get_config()

    for table in result:
        for record in table.records:
            # Create point from record
            point = Point(record.get_measurement())

            # Add all tags
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
                    point = point.tag(key, str(value))

            # Add field
            point = point.field(record.get_field(), record.get_value())

            # Add timestamp
            point = point.time(record.get_time())

            write_api.write(bucket=dest_bucket, org=config.influxdb_org, record=point)
            records_copied += 1

            if records_copied % 100 == 0:
                print(f"  Copied: {records_copied} records...", end="\r")

    write_api.close()
    print(f"  Copied: {records_copied} records (DONE)     ")
    return records_copied


def main():
    """Main entry point."""
    args = parse_args()

    # Calculate time range
    end_time = datetime.utcnow()
    if args.minutes:
        start_time = end_time - timedelta(minutes=args.minutes)
        print(f"Copying last {args.minutes} minutes of data")
    else:
        start_time = end_time - timedelta(hours=args.hours)
        print(f"Copying last {args.hours} hours of data")

    if args.dry_run:
        print("DRY-RUN MODE: No data will be copied")

    print("=" * 60)

    # Load config
    config = get_config()

    # Connect to InfluxDB
    print(f"\nConnecting to InfluxDB: {config.influxdb_url}")
    client = InfluxDBClient(
        url=config.influxdb_url, token=config.influxdb_token, org=config.influxdb_org
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
            print("\nTest buckets populated with staging data!")
            print("You can now test aggregation with real data locally.")

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
