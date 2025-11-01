#!/usr/bin/env python3
"""Script to create test buckets in InfluxDB."""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.common.config import get_config
import influxdb_client
from influxdb_client.client.bucket_api import BucketsApi


def list_buckets():
    """List all existing buckets."""
    print("Listing existing buckets...")
    try:
        config = get_config()
        client = influxdb_client.InfluxDBClient(
            url=config.influxdb_url,
            token=config.influxdb_token,
            org=config.influxdb_org
        )
        buckets_api = client.buckets_api()
        buckets = buckets_api.find_buckets().buckets

        print(f"\nFound {len(buckets)} buckets:")
        for bucket in buckets:
            print(f"  - {bucket.name}")

        client.close()
        return buckets

    except Exception as e:
        print(f"Error listing buckets: {e}")
        return []


def create_test_buckets():
    """Create test buckets if they don't exist."""
    print("\nCreating test buckets...")

    test_buckets = [
        ("temperatures_test", "Temperature sensor data (test)"),
        ("weather_test", "Weather forecast data (test)"),
        ("spotprice_test", "Electricity spot prices (test)"),
        ("emeters_test", "Energy meter data (test)"),
        ("checkwatt_full_data_test", "CheckWatt battery/solar data (test)"),
        ("load_control_test", "Load control heating programs (test)"),
    ]

    try:
        config = get_config()
        client = influxdb_client.InfluxDBClient(
            url=config.influxdb_url,
            token=config.influxdb_token,
            org=config.influxdb_org
        )
        buckets_api = client.buckets_api()

        # Get existing buckets
        existing = buckets_api.find_buckets().buckets
        existing_names = [b.name for b in existing]

        created_count = 0
        skipped_count = 0

        for bucket_name, description in test_buckets:
            if bucket_name in existing_names:
                print(f"  Skip: {bucket_name} (already exists)")
                skipped_count += 1
            else:
                try:
                    # Create with 30 day retention (2592000 seconds)
                    retention_rules = influxdb_client.domain.retention_rule.RetentionRule(
                        type="expire",
                        every_seconds=2592000  # 30 days
                    )

                    bucket = influxdb_client.domain.bucket.Bucket(
                        name=bucket_name,
                        retention_rules=[retention_rules],
                        description=description,
                        org_id=client.org
                    )

                    buckets_api.create_bucket(bucket=bucket)
                    print(f"  Created: {bucket_name}")
                    created_count += 1

                except Exception as e:
                    print(f"  Error creating {bucket_name}: {e}")

        client.close()

        print(f"\nSummary:")
        print(f"  Created: {created_count}")
        print(f"  Skipped: {skipped_count}")
        print(f"  Total: {len(test_buckets)}")

        return True

    except Exception as e:
        print(f"Error creating buckets: {e}")
        return False


def main():
    """Main entry point."""
    print("=" * 60)
    print("InfluxDB Test Bucket Setup")
    print("=" * 60)

    if not os.path.exists('.env'):
        print("\nERROR: .env file not found!")
        print("Please create .env with your InfluxDB credentials.")
        return 1

    # List existing buckets
    list_buckets()

    # Create test buckets
    print("\n" + "=" * 60)
    success = create_test_buckets()

    print("=" * 60)

    if success:
        print("\nTest buckets are ready!")
        print("You can now run: python tests/integration/test_influx_connection.py")
        return 0
    else:
        print("\nFailed to create test buckets. Check your credentials and permissions.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
