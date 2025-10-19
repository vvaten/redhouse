"""Unit tests for JSONDataLogger."""

import datetime
import json
import tempfile
import time
import unittest

from src.common.json_logger import JSONDataLogger


class TestJSONDataLogger(unittest.TestCase):
    """Test cases for JSONDataLogger class."""

    def setUp(self):
        """Set up test fixtures."""
        # Create temporary directory for test logs
        self.temp_dir = tempfile.mkdtemp()
        self.logger = JSONDataLogger("test_source", log_dir=self.temp_dir)

    def tearDown(self):
        """Clean up test files."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_initialization(self):
        """Test logger initialization."""
        self.assertEqual(self.logger.data_source_name, "test_source")
        self.assertEqual(self.logger.retention_days, 7)
        self.assertTrue(self.logger.log_dir.exists())

    def test_log_data_creates_file(self):
        """Test that log_data creates a JSON file."""
        data = {"temperature": 22.5, "humidity": 45}
        metadata = {"sensor": "test_sensor"}

        success = self.logger.log_data(data, metadata)

        self.assertTrue(success)
        log_files = list(self.logger.log_dir.glob("*.json"))
        self.assertEqual(len(log_files), 1)

    def test_log_data_content(self):
        """Test that logged data has correct structure."""
        data = {"value": 123, "status": "ok"}
        metadata = {"count": 10}

        self.logger.log_data(data, metadata)

        log_files = list(self.logger.log_dir.glob("*.json"))
        with open(log_files[0]) as f:
            logged = json.load(f)

        self.assertIn("timestamp", logged)
        self.assertIn("data_source", logged)
        self.assertIn("metadata", logged)
        self.assertIn("data", logged)

        self.assertEqual(logged["data_source"], "test_source")
        self.assertEqual(logged["data"], data)
        self.assertEqual(logged["metadata"], metadata)

    def test_log_filename_format(self):
        """Test that log filename follows YYYYMMDD_HHMMSS.json format."""
        self.logger.log_data({"test": "data"})

        log_files = list(self.logger.log_dir.glob("*.json"))
        filename = log_files[0].name

        # Check format: YYYYMMDD_HHMMSS.json
        self.assertTrue(filename.endswith(".json"))
        parts = filename[:-5].split("_")
        self.assertEqual(len(parts), 2)
        self.assertEqual(len(parts[0]), 8)  # YYYYMMDD
        self.assertEqual(len(parts[1]), 6)  # HHMMSS

    def test_multiple_logs(self):
        """Test logging multiple data points (may overwrite within same second)."""
        # Log 5 times - they may have same timestamp and overwrite
        for i in range(5):
            success = self.logger.log_data({"value": i})
            self.assertTrue(success)

        # Should have at least 1 log file (may be fewer if timestamps collide)
        log_files = list(self.logger.log_dir.glob("*.json"))
        self.assertGreaterEqual(len(log_files), 1)
        self.assertLessEqual(len(log_files), 5)

    def test_cleanup_old_logs(self):
        """Test cleanup of old log files."""
        # Create some log files with different ages
        now = time.time()

        # Create 3 recent files (within retention)
        for i in range(3):
            log_file = self.logger.log_dir / f"recent_{i}.json"
            log_file.write_text('{"data": "recent"}')
            # Touch file to set mtime to now
            log_file.touch()

        # Create 2 old files (beyond retention)
        old_time = now - (self.logger.retention_days + 1) * 86400
        for i in range(2):
            log_file = self.logger.log_dir / f"old_{i}.json"
            log_file.write_text('{"data": "old"}')
            # Set mtime to old time
            import os

            os.utime(log_file, (old_time, old_time))

        # Should have 5 files total
        all_files = list(self.logger.log_dir.glob("*.json"))
        self.assertEqual(len(all_files), 5)

        # Run cleanup
        deleted_count = self.logger.cleanup_old_logs()

        # Should delete 2 old files
        self.assertEqual(deleted_count, 2)

        # Should have 3 files remaining
        remaining_files = list(self.logger.log_dir.glob("*.json"))
        self.assertEqual(len(remaining_files), 3)

    def test_get_recent_logs(self):
        """Test getting recent log files."""
        # Create files at different times
        now = time.time()

        # Create 3 recent files
        recent_files = []
        for i in range(3):
            log_file = self.logger.log_dir / f"recent_{i}.json"
            log_file.write_text('{"data": "recent"}')
            # Set different mtimes (newest to oldest)
            mtime = now - (i * 3600)  # 1 hour apart
            import os

            os.utime(log_file, (mtime, mtime))
            recent_files.append(log_file)

        # Create 1 old file (beyond 7 days)
        old_time = now - (10 * 86400)
        old_file = self.logger.log_dir / "old_file.json"
        old_file.write_text('{"data": "old"}')
        import os

        os.utime(old_file, (old_time, old_time))

        # Get recent logs (last 7 days)
        recent_logs = self.logger.get_recent_logs(days=7)

        # Should return 3 recent files, sorted by mtime (newest first)
        self.assertEqual(len(recent_logs), 3)
        # Check they're sorted newest first
        self.assertEqual(recent_logs[0].name, "recent_0.json")
        self.assertEqual(recent_logs[1].name, "recent_1.json")
        self.assertEqual(recent_logs[2].name, "recent_2.json")

    def test_load_log(self):
        """Test loading a log file."""
        data = {"temperature": 25.0, "readings": [1, 2, 3]}
        metadata = {"sensor_id": "abc123"}

        self.logger.log_data(data, metadata)

        log_files = list(self.logger.log_dir.glob("*.json"))
        loaded = self.logger.load_log(log_files[0])

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["data"], data)
        self.assertEqual(loaded["metadata"], metadata)

    def test_load_nonexistent_log(self):
        """Test loading a non-existent log file."""
        fake_path = self.logger.log_dir / "nonexistent.json"
        loaded = self.logger.load_log(fake_path)

        self.assertIsNone(loaded)

    def test_custom_retention_days(self):
        """Test setting custom retention days."""
        logger = JSONDataLogger("test_source", log_dir=self.temp_dir)
        logger.retention_days = 30

        self.assertEqual(logger.retention_days, 30)

    def test_log_data_with_datetime_objects(self):
        """Test logging data with datetime objects (should be serialized)."""
        now = datetime.datetime.now()
        data = {"timestamp": now, "value": 42}

        success = self.logger.log_data(data)
        self.assertTrue(success)

        log_files = list(self.logger.log_dir.glob("*.json"))
        with open(log_files[0]) as f:
            logged = json.load(f)

        # Datetime should be serialized as string
        self.assertIsInstance(logged["data"]["timestamp"], str)


if __name__ == "__main__":
    unittest.main()
