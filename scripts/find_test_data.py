#!/usr/bin/env python3
"""Script to find TestSensor data timestamps."""

import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import influxdb_client

from src.common.config import get_config


def find_test_data():
    """Find all TestSensor data in production bucket."""
    print("Searching for TestSensor data in production bucket...")

    try:
        config = get_config()
        client = influxdb_client.InfluxDBClient(
            url=config.influxdb_url, token=config.influxdb_token, org=config.influxdb_org
        )

        query_api = client.query_api()
        bucket = "temperatures"

        # Query for TestSensor data in last 7 days
        query = f"""
        from(bucket: "{bucket}")
          |> range(start: -7d)
          |> filter(fn: (r) => r["_measurement"] == "temperatures")
          |> filter(fn: (r) => r["_field"] =~ /TestSensor/)
        """

        tables = query_api.query(query, org=config.influxdb_org)

        found = False
        for table in tables:
            for record in table.records:
                if not found:
                    print("\nFound TestSensor data:")
                    found = True

                field = record.get_field()
                value = record.get_value()
                timestamp = record.get_time()

                print(f"  {timestamp} - {field}: {value}")

        if not found:
            print("\nNo TestSensor data found in last 7 days!")
            print("It may have been already cleaned up.")

        client.close()
        return 0

    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(find_test_data())
