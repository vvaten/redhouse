"""Unit tests for CheckWatt data collection."""

import datetime
import unittest
from unittest.mock import AsyncMock, patch

from src.data_collection.checkwatt import (
    CHECKWATT_COLUMNS,
    _backup_raw_data,
    _compute_date_range,
    _load_and_validate_credentials,
    _validate_and_process_response,
    collect_checkwatt_data,
    format_datetime,
    get_auth_token,
    process_checkwatt_data,
)


class TestCheckWattCollection(unittest.TestCase):
    """Test CheckWatt collection functions."""

    def test_checkwatt_columns_no_unicode(self):
        """Verify column names contain no unicode characters."""
        for col in CHECKWATT_COLUMNS:
            self.assertTrue(col.isascii(), f"Column {col} contains non-ASCII characters")

    @patch("aiohttp.ClientSession")
    async def test_get_auth_token_success(self, mock_session_class):
        """Test successful authentication."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"JwtToken": "test_token_123"})

        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.post.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value = mock_session

        result = await get_auth_token("user@example.com", "password123")

        self.assertEqual(result, "test_token_123")

    @patch("aiohttp.ClientSession")
    async def test_get_auth_token_failure(self, mock_session_class):
        """Test authentication failure."""
        mock_response = AsyncMock()
        mock_response.status = 401
        mock_response.text = AsyncMock(return_value="Unauthorized")

        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.post.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value = mock_session

        result = await get_auth_token("user@example.com", "wrong_password")

        self.assertIsNone(result)

    def test_format_datetime_string(self):
        """Test formatting of ISO string."""
        result = format_datetime("2025-10-18T14:00:00")
        self.assertEqual(result, "2025-10-18T14:00:00")

    def test_format_datetime_object(self):
        """Test formatting of datetime object."""
        dt = datetime.datetime(2025, 10, 18, 14, 0, 0)
        result = format_datetime(dt)
        self.assertEqual(result, "2025-10-18T14:00:00")

    def test_format_datetime_invalid(self):
        """Test error on invalid format."""
        with self.assertRaises(ValueError):
            format_datetime(12345)

    def test_process_checkwatt_data_success(self):
        """Test processing of CheckWatt JSON data."""
        json_data = {
            "Grouping": "delta",
            "DateFrom": "2025-10-18T14:00:00",
            "Meters": [
                {  # Battery_SoC
                    "Measurements": [{"Value": 50.0}, {"Value": 51.0}, {"Value": 52.0}]
                },
                {  # BatteryCharge
                    "Measurements": [{"Value": 100.0}, {"Value": 110.0}, {"Value": 120.0}]
                },
                {  # BatteryDischarge
                    "Measurements": [{"Value": 0.0}, {"Value": 0.0}, {"Value": 0.0}]
                },
                {  # EnergyImport
                    "Measurements": [{"Value": 200.0}, {"Value": 210.0}, {"Value": 220.0}]
                },
                {  # EnergyExport
                    "Measurements": [{"Value": 50.0}, {"Value": 55.0}, {"Value": 60.0}]
                },
                {  # SolarYield
                    "Measurements": [{"Value": 300.0}, {"Value": 310.0}, {"Value": 320.0}]
                },
            ],
        }

        result = process_checkwatt_data(json_data)

        # Should have 3 data points
        self.assertEqual(len(result), 3)

        # Check first data point
        self.assertEqual(result[0]["Battery_SoC"], 50.0)
        self.assertEqual(result[0]["BatteryCharge"], 100.0)
        self.assertEqual(result[0]["SolarYield"], 300.0)
        self.assertIn("epoch_timestamp", result[0])

        # Check timestamps are 60 seconds apart
        self.assertEqual(result[1]["epoch_timestamp"] - result[0]["epoch_timestamp"], 60)
        self.assertEqual(result[2]["epoch_timestamp"] - result[1]["epoch_timestamp"], 60)

        # Last record should only have Battery_SoC
        self.assertEqual(result[2]["Battery_SoC"], 52.0)
        self.assertNotIn("BatteryCharge", result[2])
        self.assertNotIn("SolarYield", result[2])

    def test_process_checkwatt_data_wrong_grouping(self):
        """Test error on wrong grouping type."""
        json_data = {"Grouping": "average", "Meters": []}  # Not "delta"

        with self.assertRaises(ValueError) as ctx:
            process_checkwatt_data(json_data)

        self.assertIn("delta grouping", str(ctx.exception))

    def test_process_checkwatt_data_wrong_meter_count(self):
        """Test error on wrong number of meters."""
        json_data = {"Grouping": "delta", "Meters": [{"Measurements": []}]}  # Only 1 meter, need 6

        with self.assertRaises(ValueError) as ctx:
            process_checkwatt_data(json_data)

        self.assertIn("meters", str(ctx.exception).lower())

    def test_load_and_validate_credentials_success(self):
        """Test successful credential loading."""
        mock_config = {
            "checkwatt_username": "user@example.com",
            "checkwatt_password": "password123",
            "checkwatt_meter_ids": "meter1, meter2, meter3",
        }

        username, password, meter_ids = _load_and_validate_credentials(mock_config)

        self.assertEqual(username, "user@example.com")
        self.assertEqual(password, "password123")
        self.assertEqual(meter_ids, ["meter1", "meter2", "meter3"])

    def test_load_and_validate_credentials_missing_username(self):
        """Test error when username is missing."""
        mock_config = {"checkwatt_password": "password123", "checkwatt_meter_ids": "meter1"}

        with self.assertRaises(ValueError) as ctx:
            _load_and_validate_credentials(mock_config)

        self.assertIn("USERNAME", str(ctx.exception))

    def test_load_and_validate_credentials_missing_password(self):
        """Test error when password is missing."""
        mock_config = {"checkwatt_username": "user@example.com", "checkwatt_meter_ids": "meter1"}

        with self.assertRaises(ValueError) as ctx:
            _load_and_validate_credentials(mock_config)

        self.assertIn("PASSWORD", str(ctx.exception))

    def test_load_and_validate_credentials_missing_meter_ids(self):
        """Test error when meter IDs are missing."""
        mock_config = {
            "checkwatt_username": "user@example.com",
            "checkwatt_password": "password123",
        }

        with self.assertRaises(ValueError) as ctx:
            _load_and_validate_credentials(mock_config)

        self.assertIn("METER_IDS", str(ctx.exception))

    def test_compute_date_range_last_hour_only(self):
        """Test date range computation for last hour."""
        start, end = _compute_date_range(last_hour_only=True, start_date=None, end_date=None)

        # Should be ISO format strings
        self.assertIsInstance(start, str)
        self.assertIsInstance(end, str)
        self.assertEqual(len(start), 19)
        self.assertEqual(len(end), 19)

    def test_compute_date_range_default(self):
        """Test date range computation with defaults."""
        start, end = _compute_date_range(last_hour_only=False, start_date=None, end_date=None)

        # Should be today to tomorrow
        self.assertIn("T00:00:00", start)
        self.assertIn("T00:00:00", end)

    def test_compute_date_range_custom(self):
        """Test date range computation with custom dates."""
        start, end = _compute_date_range(
            last_hour_only=False,
            start_date="2025-10-18T14:00:00",
            end_date="2025-10-19T14:00:00",
        )

        self.assertEqual(start, "2025-10-18T14:00:00")
        self.assertEqual(end, "2025-10-19T14:00:00")

    @patch("src.data_collection.checkwatt.JSONDataLogger")
    def test_backup_raw_data(self, mock_logger_class):
        """Test raw data backup."""
        mock_logger = mock_logger_class.return_value

        json_data = {"Meters": [{"data": 1}, {"data": 2}]}
        _backup_raw_data(json_data, "2025-10-18T14:00:00", "2025-10-19T14:00:00")

        mock_logger_class.assert_called_once_with("checkwatt")
        mock_logger.log_data.assert_called_once()
        mock_logger.cleanup_old_logs.assert_called_once()

    def test_validate_and_process_response_success(self):
        """Test successful response validation and processing."""
        json_data = {
            "Grouping": "delta",
            "DateFrom": "2025-10-18T14:00:00",
            "DateTo": "2025-10-18T15:00:00",
            "Meters": [
                {"Measurements": [{"Value": 50.0}] * 15},
                {"Measurements": [{"Value": 100.0}] * 15},
                {"Measurements": [{"Value": 0.0}] * 15},
                {"Measurements": [{"Value": 200.0}] * 15},
                {"Measurements": [{"Value": 50.0}] * 15},
                {"Measurements": [{"Value": 300.0}] * 15},
            ],
        }

        result = _validate_and_process_response(json_data)

        self.assertEqual(len(result), 15)
        self.assertIn("Battery_SoC", result[0])

    def test_validate_and_process_response_wrong_field_count(self):
        """Test error on wrong number of fields."""
        json_data = {"Field1": "value", "Field2": "value"}

        with self.assertRaises(ValueError) as ctx:
            _validate_and_process_response(json_data)

        self.assertIn("4 fields", str(ctx.exception))

    def test_validate_and_process_response_insufficient_data(self):
        """Test error on insufficient data points."""
        json_data = {
            "Grouping": "delta",
            "DateFrom": "2025-10-18T14:00:00",
            "DateTo": "2025-10-18T15:00:00",
            "Meters": [
                {"Measurements": [{"Value": 50.0}] * 5},
                {"Measurements": [{"Value": 100.0}] * 5},
                {"Measurements": [{"Value": 0.0}] * 5},
                {"Measurements": [{"Value": 200.0}] * 5},
                {"Measurements": [{"Value": 50.0}] * 5},
                {"Measurements": [{"Value": 300.0}] * 5},
            ],
        }

        with self.assertRaises(ValueError) as ctx:
            _validate_and_process_response(json_data)

        self.assertIn("Too little data", str(ctx.exception))

    @patch("src.data_collection.checkwatt.write_checkwatt_to_influx")
    @patch("src.data_collection.checkwatt.fetch_checkwatt_data")
    @patch("src.data_collection.checkwatt.get_auth_token")
    @patch("src.data_collection.checkwatt._backup_raw_data")
    @patch("src.data_collection.checkwatt.get_config")
    async def test_collect_checkwatt_data_success(
        self, mock_get_config, mock_backup, mock_get_auth, mock_fetch, mock_write
    ):
        """Test successful data collection."""
        mock_config = {
            "checkwatt_username": "user@example.com",
            "checkwatt_password": "password123",
            "checkwatt_meter_ids": "meter1,meter2,meter3",
        }
        mock_get_config.return_value = mock_config
        mock_get_auth.return_value = "test_token"

        json_data = {
            "Grouping": "delta",
            "DateFrom": "2025-10-18T14:00:00",
            "DateTo": "2025-10-18T15:00:00",
            "Meters": [
                {"Measurements": [{"Value": 50.0}] * 15},
                {"Measurements": [{"Value": 100.0}] * 15},
                {"Measurements": [{"Value": 0.0}] * 15},
                {"Measurements": [{"Value": 200.0}] * 15},
                {"Measurements": [{"Value": 50.0}] * 15},
                {"Measurements": [{"Value": 300.0}] * 15},
            ],
        }
        mock_fetch.return_value = json_data
        mock_write.return_value = True

        result = await collect_checkwatt_data(dry_run=False)

        self.assertEqual(result, 0)
        mock_get_auth.assert_called_once()
        mock_fetch.assert_called_once()
        mock_backup.assert_called_once()
        mock_write.assert_called_once()

    @patch("src.data_collection.checkwatt.get_config")
    async def test_collect_checkwatt_data_missing_credentials(self, mock_get_config):
        """Test error when credentials are missing."""
        mock_config = {}
        mock_get_config.return_value = mock_config

        result = await collect_checkwatt_data()

        self.assertEqual(result, 1)

    @patch("src.data_collection.checkwatt.get_auth_token")
    @patch("src.data_collection.checkwatt.get_config")
    async def test_collect_checkwatt_data_auth_failure(self, mock_get_config, mock_get_auth):
        """Test error when authentication fails."""
        mock_config = {
            "checkwatt_username": "user@example.com",
            "checkwatt_password": "password123",
            "checkwatt_meter_ids": "meter1",
        }
        mock_get_config.return_value = mock_config
        mock_get_auth.return_value = None

        result = await collect_checkwatt_data()

        self.assertEqual(result, 1)

    @patch("src.data_collection.checkwatt.fetch_checkwatt_data")
    @patch("src.data_collection.checkwatt.get_auth_token")
    @patch("src.data_collection.checkwatt.get_config")
    async def test_collect_checkwatt_data_fetch_failure(
        self, mock_get_config, mock_get_auth, mock_fetch
    ):
        """Test error when data fetch fails."""
        mock_config = {
            "checkwatt_username": "user@example.com",
            "checkwatt_password": "password123",
            "checkwatt_meter_ids": "meter1",
        }
        mock_get_config.return_value = mock_config
        mock_get_auth.return_value = "test_token"
        mock_fetch.return_value = None

        result = await collect_checkwatt_data()

        self.assertEqual(result, 1)


# Async test runner
def run_async_test(coro):
    """Helper to run async tests."""
    import asyncio

    return asyncio.run(coro)


if __name__ == "__main__":
    # Run async tests
    suite = unittest.TestLoader().loadTestsFromTestCase(TestCheckWattCollection)
    for test in suite:
        if "async" in test._testMethodName or test._testMethodName.startswith("test_get"):
            # Wrap async tests
            original_method = getattr(test, test._testMethodName)
            setattr(
                test, test._testMethodName, lambda self, m=original_method: run_async_test(m(self))
            )

    unittest.main()
