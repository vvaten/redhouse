"""Integration tests for JSON logging and replay functionality."""

import asyncio
import datetime
import json
import tempfile
import unittest
from pathlib import Path

from src.common.config import get_config
from src.common.json_logger import JSONDataLogger
from src.data_collection.spot_prices import (
    fetch_spot_prices_from_api,
    process_spot_prices,
    write_spot_prices_to_influx,
)


class TestJSONLoggingIntegration(unittest.TestCase):
    """Integration tests for JSON logging with real data collectors."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = get_config()
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up test files."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_spot_prices_json_logging(self):
        """Test that spot prices are logged to JSON during collection."""
        # Create logger
        json_logger = JSONDataLogger("spot_prices_test", log_dir=self.temp_dir)

        # Fetch real spot price data
        spot_prices_raw = asyncio.run(fetch_spot_prices_from_api())

        self.assertIsNotNone(spot_prices_raw)
        self.assertGreater(len(spot_prices_raw), 0)

        # Log the data
        success = json_logger.log_data(
            spot_prices_raw, metadata={"num_prices": len(spot_prices_raw)}
        )

        self.assertTrue(success)

        # Verify log file was created
        log_files = list(json_logger.log_dir.glob("*.json"))
        self.assertEqual(len(log_files), 1)

        # Load and verify log content
        loaded = json_logger.load_log(log_files[0])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["data_source"], "spot_prices_test")
        self.assertEqual(len(loaded["data"]), len(spot_prices_raw))

    def test_json_log_replay_to_test_bucket(self):
        """Test replaying JSON logs to InfluxDB test bucket."""
        # Skip if not in test environment
        from src.common.config_validator import ConfigValidator

        env_checks = ConfigValidator.check_environment(self.config)

        # Check if we're using test buckets
        if not any("_test" in str(check) for check in env_checks):
            self.skipTest("Not in test environment (requires test buckets)")

        # Create logger and log some data
        json_logger = JSONDataLogger("spot_prices_test", log_dir=self.temp_dir)

        # Fetch and process real spot price data
        spot_prices_raw = asyncio.run(fetch_spot_prices_from_api())
        self.assertIsNotNone(spot_prices_raw)

        # Log it
        json_logger.log_data(spot_prices_raw, metadata={"source": "integration_test"})

        # Load the log
        log_files = json_logger.get_recent_logs(days=1)
        self.assertEqual(len(log_files), 1)

        loaded = json_logger.load_log(log_files[0])
        self.assertIsNotNone(loaded)

        # Process the data
        processed = process_spot_prices(loaded["data"], self.config)
        self.assertGreater(len(processed), 0)

        # Write to test bucket
        latest_timestamp = asyncio.run(
            write_spot_prices_to_influx(processed, dry_run=False)
        )

        self.assertIsNotNone(latest_timestamp)
        print(f"Successfully wrote {len(processed)} spot prices to test bucket")

    def test_json_log_cleanup(self):
        """Test that old JSON logs are cleaned up properly."""
        json_logger = JSONDataLogger("cleanup_test", log_dir=self.temp_dir)
        json_logger.retention_days = 7

        # Create some log files
        now = datetime.datetime.now()

        # Create 5 recent logs
        for i in range(5):
            json_logger.log_data({"value": i, "type": "recent"})

        # Create 3 old logs manually with old timestamps
        import os
        import time

        old_time = time.time() - (10 * 86400)  # 10 days ago

        for i in range(3):
            old_file = json_logger.log_dir / f"old_test_{i}.json"
            old_file.write_text(json.dumps({"data": {"value": i, "type": "old"}}))
            os.utime(old_file, (old_time, old_time))

        # Should have up to 8 files (5 recent + 3 old)
        # Note: recent files may have same timestamp and overwrite
        all_files = list(json_logger.log_dir.glob("*.json"))
        self.assertGreaterEqual(len(all_files), 3)  # At least the 3 old files

        # Run cleanup
        deleted = json_logger.cleanup_old_logs()

        # Should delete the 3 old files
        self.assertEqual(deleted, 3)

        # Verify old files are gone
        remaining = list(json_logger.log_dir.glob("*.json"))
        for f in remaining:
            self.assertFalse(f.name.startswith("old_test_"))


if __name__ == "__main__":
    unittest.main()
