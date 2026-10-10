"""Integration test for InfluxDB connection and write operations."""

import datetime
import os
import sys

# Add src to path (required before project imports)
# ruff: noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.common.config import get_config  # noqa: E402
from src.common.influx_client import InfluxClient  # noqa: E402


def test_influx_connection():
    """Test basic InfluxDB connection."""
    print("Testing InfluxDB connection...")

    try:
        config = get_config()
        print(f"  URL: {config.influxdb_url}")
        print(f"  Org: {config.influxdb_org}")
        print(f"  Temperature bucket: {config.influxdb_bucket_temperatures}")

        InfluxClient(config)
        print("  Connection: OK")

        return True

    except Exception as e:
        print(f"  Connection: FAILED - {e}")
        return False


def test_write_temperature():
    """Test writing temperature data to test bucket."""
    print("\nTesting temperature write to test bucket...")

    try:
        config = get_config()
        influx = InfluxClient(config)

        # Simulate temperature data
        test_fields = {"TestSensor1": 21.5, "TestSensor2": 22.0, "TestSensor3": 19.8}

        timestamp = datetime.datetime.utcnow()

        success = influx.write_point(
            measurement="temperatures", fields=test_fields, timestamp=timestamp
        )

        if success:
            print("  Write: OK")
            print(f"  Wrote {len(test_fields)} test sensors at {timestamp}")
            print(f"  Bucket: {config.influxdb_bucket_temperatures}")
            return True
        else:
            print("  Write: FAILED")
            return False

    except Exception as e:
        print(f"  Write: FAILED - {e}")
        return False
