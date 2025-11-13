#!/usr/bin/env python3
"""Script to remove TestSensor fields from a specific timestamp by rewriting the point."""

import sys
import os
from datetime import datetime, timedelta

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.config import get_config
import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS


def fix_test_data(timestamp_str, confirm=False, dry_run=False):
    """
    Remove TestSensor fields from a specific timestamp.

    Strategy:
    1. Query all data at the exact timestamp
    2. Filter out TestSensor fields
    3. Delete the original point (2-second window)
    4. Write back the cleaned data (if there's any real data)

    Args:
        timestamp_str: ISO timestamp like "2025-10-18T18:31:00.000Z"
        confirm: If True, actually perform the fix
        dry_run: If True, only show what would be done
    """
    print("=" * 60)
    print("Fix TestSensor Data in Production Bucket")
    print("=" * 60)

    try:
        config = get_config()
        client = influxdb_client.InfluxDBClient(
            url=config.influxdb_url, token=config.influxdb_token, org=config.influxdb_org
        )

        query_api = client.query_api()
        delete_api = client.delete_api()
        write_api = client.write_api(write_options=SYNCHRONOUS)

        bucket = "temperatures"

        # Parse the timestamp
        target_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))

        print(f"\nTarget bucket: {bucket}")
        print(f"Target timestamp: {target_time}")
        print(f"Searching for data at this timestamp...")

        # Query data at this exact timestamp (with 2-second window)
        start_time = (target_time - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        stop_time = (target_time + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        query = f"""
        from(bucket: "{bucket}")
          |> range(start: {start_time}, stop: {stop_time})
          |> filter(fn: (r) => r["_measurement"] == "temperatures")
        """

        tables = query_api.query(query, org=config.influxdb_org)

        # Collect all fields at this timestamp
        fields_data = {}
        exact_timestamp = None

        for table in tables:
            for record in table.records:
                field_name = record.get_field()
                value = record.get_value()
                ts = record.get_time()

                if exact_timestamp is None:
                    exact_timestamp = ts

                fields_data[field_name] = value

        if not fields_data:
            print("\nNo data found at this timestamp.")
            client.close()
            return 0

        print(f"\nFound {len(fields_data)} fields at {exact_timestamp}:")

        test_sensors = []
        real_sensors = {}

        for field, value in sorted(fields_data.items()):
            if field.startswith("TestSensor"):
                test_sensors.append(field)
                print(f"  - {field}: {value} (TEST - will be removed)")
            else:
                real_sensors[field] = value
                print(f"  - {field}: {value} (REAL - will be kept)")

        if not test_sensors:
            print("\nNo TestSensor fields found. Nothing to fix.")
            client.close()
            return 0

        print(f"\nWill remove {len(test_sensors)} test sensor(s)")
        print(f"Will keep {len(real_sensors)} real sensor(s)")

        if dry_run:
            print("\n[DRY-RUN] Would perform these steps:")
            print(f"  1. Delete all data at {exact_timestamp} (+/- 1 sec)")
            if real_sensors:
                print(f"  2. Write back {len(real_sensors)} real sensors")
            else:
                print("  2. No real sensors to write back")
            print("\nRun with --confirm to actually fix")
            client.close()
            return 0

        if not confirm:
            print("\nTo fix, run with --confirm flag")
            print("To see what would be done, run with --dry-run")
            client.close()
            return 1

        # Step 1: Delete the original point
        print(f"\nStep 1: Deleting data at {exact_timestamp}...")
        start = exact_timestamp - timedelta(seconds=1)
        stop = exact_timestamp + timedelta(seconds=1)

        delete_api.delete(
            start=start,
            stop=stop,
            predicate='_measurement="temperatures"',
            bucket=bucket,
            org=config.influxdb_org,
        )
        print("  Deleted: OK")

        # Step 2: Write back real sensors (if any)
        if real_sensors:
            print(f"\nStep 2: Writing back {len(real_sensors)} real sensors...")

            point = influxdb_client.Point("temperatures")
            for field_name, value in real_sensors.items():
                point = point.field(field_name, float(value))
            point = point.time(exact_timestamp)

            write_api.write(bucket=bucket, org=config.influxdb_org, record=point)
            print("  Written: OK")
        else:
            print("\nStep 2: No real sensors to write back (skipped)")

        print("\n" + "=" * 60)
        print("Fix completed successfully!")
        print(f"Removed: {', '.join(test_sensors)}")
        if real_sensors:
            print(f"Kept: {', '.join(real_sensors.keys())}")
        print("=" * 60)

        client.close()
        return 0

    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()
        return 1


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Remove TestSensor fields from a specific timestamp"
    )
    parser.add_argument("timestamp", help='ISO timestamp like "2025-10-18T18:31:00.000Z"')
    parser.add_argument(
        "--confirm", action="store_true", help="Confirm fix (required to actually fix)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be done without actually doing it"
    )

    args = parser.parse_args()

    if not os.path.exists(".env"):
        print("\nERROR: .env file not found!")
        return 1

    return fix_test_data(args.timestamp, confirm=args.confirm, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
