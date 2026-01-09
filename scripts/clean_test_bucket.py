#!/usr/bin/env python3
"""Clean a test bucket by deleting all data."""

import sys
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient

from src.common.config import get_config


def clean_bucket(bucket_name: str):
    """Delete all data from a bucket."""
    config = get_config()

    client = InfluxDBClient(
        url=config.influxdb_url, token=config.influxdb_token, org=config.influxdb_org
    )

    delete_api = client.delete_api()

    start = "1970-01-01T00:00:00Z"
    stop = datetime.now(timezone.utc).isoformat()

    print(f"Deleting all data from bucket '{bucket_name}' between {start} and {stop}")

    try:
        delete_api.delete(
            start=start, stop=stop, predicate="", bucket=bucket_name, org=config.influxdb_org
        )
        print(f"Successfully cleaned bucket '{bucket_name}'")
    except Exception as e:
        print(f"Error cleaning bucket: {e}")
        sys.exit(1)
    finally:
        client.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python clean_test_bucket.py <bucket_name>")
        sys.exit(1)

    bucket_name = sys.argv[1]
    if not bucket_name.endswith("_test"):
        print(f"Error: Bucket name must end with '_test' for safety. Got: {bucket_name}")
        sys.exit(1)

    clean_bucket(bucket_name)
