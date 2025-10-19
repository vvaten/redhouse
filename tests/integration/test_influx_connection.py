"""Integration test for InfluxDB connection and write operations."""

import datetime
import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.common.config import get_config
from src.common.influx_client import InfluxClient


def test_influx_connection():
    """Test basic InfluxDB connection."""
    print("Testing InfluxDB connection...")

    try:
        config = get_config()
        print(f"  URL: {config.influxdb_url}")
        print(f"  Org: {config.influxdb_org}")
        print(f"  Temperature bucket: {config.influxdb_bucket_temperatures}")

        influx = InfluxClient(config)
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


def main():
    """Run integration tests."""
    print("=" * 60)
    print("InfluxDB Integration Tests")
    print("=" * 60)

    # Check if .env exists
    if not os.path.exists(".env"):
        print("\nERROR: .env file not found!")
        print("Please copy .env.test to .env and add your credentials:")
        print("  cp .env.test .env")
        print("  nano .env  # Edit with actual token")
        return 1

    results = []

    # Test 1: Connection
    results.append(("Connection", test_influx_connection()))

    # Test 2: Write
    results.append(("Write", test_write_temperature()))

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    all_passed = True
    for test_name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {test_name}: {status}")
        if not passed:
            all_passed = False

    print("=" * 60)

    if all_passed:
        print("\nAll tests passed! You can now:")
        print("  1. Check your test bucket in Grafana")
        print("  2. Run: python collect_temperatures.py --dry-run")
        print("  3. Deploy to Raspberry Pi when ready")
        return 0
    else:
        print("\nSome tests failed. Please check:")
        print("  1. InfluxDB is running at the configured URL")
        print("  2. Token has write permissions")
        print("  3. Test buckets exist in InfluxDB")
        return 1


if __name__ == "__main__":
    sys.exit(main())
