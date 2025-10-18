#!/usr/bin/env python3
"""Integration test for safety system - prevents test data in production."""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.common.config import get_config
from src.common.influx_client import InfluxClient


def test_safety_blocks_test_data():
    """Verify safety system blocks test data from going to production."""
    print("=" * 60)
    print("Safety System Integration Test")
    print("=" * 60)

    try:
        config = get_config()

        print(f"\nCurrent configuration:")
        print(f"  Temperatures bucket: {config.influxdb_bucket_temperatures}")

        # Check if using production bucket
        if config.influxdb_bucket_temperatures == "temperatures":
            print(f"\n*** PRODUCTION BUCKET DETECTED ***")
            print(f"Testing safety system...")

            influx = InfluxClient(config)

            # Try to write test data (should be blocked)
            test_fields = {
                "TestSensor1": 99.9,
                "TestSensor2": 99.9
            }

            print(f"\nAttempting to write test data to production bucket...")
            print(f"  Fields: {test_fields}")

            success = influx.write_point(
                measurement="temperatures",
                fields=test_fields
            )

            if success:
                print(f"\n[FAIL] SAFETY SYSTEM FAILED!")
                print(f"Test data was written to production bucket!")
                return 1
            else:
                print(f"\n[PASS] SAFETY SYSTEM WORKS!")
                print(f"Test data was blocked from production bucket!")
                return 0

        else:
            print(f"\nTest bucket detected: {config.influxdb_bucket_temperatures}")
            print(f"Safety system only activates for production buckets.")
            print(f"No test needed.")
            return 0

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        return 1


def main():
    """Run safety system test."""
    if not os.path.exists('.env'):
        print("\nERROR: .env file not found!")
        return 1

    return test_safety_blocks_test_data()


if __name__ == "__main__":
    sys.exit(main())
