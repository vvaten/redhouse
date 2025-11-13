#!/usr/bin/env python3
"""Script to delete test sensor data from InfluxDB."""

import sys
import os
from datetime import datetime, timedelta

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.config import get_config
import influxdb_client
from influxdb_client.client.delete_api import DeleteApi


def delete_test_sensors(confirm=False, dry_run=False):
    """Delete TestSensor* data from production bucket."""
    print("=" * 60)
    print("Cleanup Test Sensor Data from Production Bucket")
    print("=" * 60)

    try:
        config = get_config()
        client = influxdb_client.InfluxDBClient(
            url=config.influxdb_url, token=config.influxdb_token, org=config.influxdb_org
        )

        delete_api = client.delete_api()

        # Define time range (last 7 days to be safe)
        start = datetime.utcnow() - timedelta(days=7)
        stop = datetime.utcnow()

        bucket = "temperatures"  # Production bucket

        print(f"\nTarget bucket: {bucket}")
        print(f"Time range: {start} to {stop}")
        print(f"Deleting: TestSensor1, TestSensor2, TestSensor3")

        # Test sensor fields to delete (must delete one at a time)
        test_fields = ["TestSensor1", "TestSensor2", "TestSensor3"]

        if dry_run:
            print("\n[DRY-RUN] Would delete the following fields:")
            for field in test_fields:
                print(f"  - {field}")
            print("\nRun with --confirm to actually delete")
            client.close()
            return 0

        if not confirm:
            print("\nTo delete, run with --confirm flag")
            print("To see what would be deleted, run with --dry-run")
            client.close()
            return 1

        # Delete the data (one field at a time)
        print("\nDeleting data...")
        deleted_count = 0

        for field in test_fields:
            try:
                predicate = f'_field="{field}"'
                print(f"  Deleting {field}...")
                delete_api.delete(
                    start=start,
                    stop=stop,
                    predicate=predicate,
                    bucket=bucket,
                    org=config.influxdb_org,
                )
                deleted_count += 1
                print(f"  {field}: OK")
            except Exception as e:
                print(f"  {field}: FAILED - {e}")

        print("\n" + "=" * 60)
        print(f"Deleted {deleted_count}/{len(test_fields)} test sensor fields")
        print("=" * 60)

        client.close()
        return 0

    except Exception as e:
        print(f"\nError deleting data: {e}")
        return 1


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Delete test sensor data from InfluxDB production bucket"
    )
    parser.add_argument(
        "--confirm", action="store_true", help="Confirm deletion (required to actually delete)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )

    args = parser.parse_args()

    if not os.path.exists(".env"):
        print("\nERROR: .env file not found!")
        return 1

    return delete_test_sensors(confirm=args.confirm, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
