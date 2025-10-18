"""Unit tests for CheckWatt data collection."""

import unittest
from unittest.mock import Mock, patch, AsyncMock
import datetime

from src.data_collection.checkwatt import (
    get_auth_token,
    format_datetime,
    process_checkwatt_data,
    CHECKWATT_COLUMNS
)


class TestCheckWattCollection(unittest.TestCase):
    """Test CheckWatt collection functions."""

    def test_checkwatt_columns_no_unicode(self):
        """Verify column names contain no unicode characters."""
        for col in CHECKWATT_COLUMNS:
            self.assertTrue(col.isascii(), f"Column {col} contains non-ASCII characters")

    @patch('aiohttp.ClientSession')
    async def test_get_auth_token_success(self, mock_session_class):
        """Test successful authentication."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={'JwtToken': 'test_token_123'})

        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.post.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value = mock_session

        result = await get_auth_token('user@example.com', 'password123')

        self.assertEqual(result, 'test_token_123')

    @patch('aiohttp.ClientSession')
    async def test_get_auth_token_failure(self, mock_session_class):
        """Test authentication failure."""
        mock_response = AsyncMock()
        mock_response.status = 401
        mock_response.text = AsyncMock(return_value="Unauthorized")

        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.post.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value = mock_session

        result = await get_auth_token('user@example.com', 'wrong_password')

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
                    "Measurements": [
                        {"Value": 50.0},
                        {"Value": 51.0},
                        {"Value": 52.0}
                    ]
                },
                {  # BatteryCharge
                    "Measurements": [
                        {"Value": 100.0},
                        {"Value": 110.0},
                        {"Value": 120.0}
                    ]
                },
                {  # BatteryDischarge
                    "Measurements": [
                        {"Value": 0.0},
                        {"Value": 0.0},
                        {"Value": 0.0}
                    ]
                },
                {  # EnergyImport
                    "Measurements": [
                        {"Value": 200.0},
                        {"Value": 210.0},
                        {"Value": 220.0}
                    ]
                },
                {  # EnergyExport
                    "Measurements": [
                        {"Value": 50.0},
                        {"Value": 55.0},
                        {"Value": 60.0}
                    ]
                },
                {  # SolarYield
                    "Measurements": [
                        {"Value": 300.0},
                        {"Value": 310.0},
                        {"Value": 320.0}
                    ]
                }
            ]
        }

        result = process_checkwatt_data(json_data)

        # Should have 3 data points
        self.assertEqual(len(result), 3)

        # Check first data point
        self.assertEqual(result[0]['Battery_SoC'], 50.0)
        self.assertEqual(result[0]['BatteryCharge'], 100.0)
        self.assertEqual(result[0]['SolarYield'], 300.0)
        self.assertIn('epoch_timestamp', result[0])

        # Check timestamps are 60 seconds apart
        self.assertEqual(result[1]['epoch_timestamp'] - result[0]['epoch_timestamp'], 60)
        self.assertEqual(result[2]['epoch_timestamp'] - result[1]['epoch_timestamp'], 60)

        # Last record should only have Battery_SoC
        self.assertEqual(result[2]['Battery_SoC'], 52.0)
        self.assertNotIn('BatteryCharge', result[2])
        self.assertNotIn('SolarYield', result[2])

    def test_process_checkwatt_data_wrong_grouping(self):
        """Test error on wrong grouping type."""
        json_data = {
            "Grouping": "average",  # Not "delta"
            "Meters": []
        }

        with self.assertRaises(ValueError) as ctx:
            process_checkwatt_data(json_data)

        self.assertIn("delta grouping", str(ctx.exception))

    def test_process_checkwatt_data_wrong_meter_count(self):
        """Test error on wrong number of meters."""
        json_data = {
            "Grouping": "delta",
            "Meters": [{"Measurements": []}]  # Only 1 meter, need 6
        }

        with self.assertRaises(ValueError) as ctx:
            process_checkwatt_data(json_data)

        self.assertIn("meters", str(ctx.exception).lower())


# Async test runner
def run_async_test(coro):
    """Helper to run async tests."""
    import asyncio
    return asyncio.run(coro)


if __name__ == '__main__':
    # Run async tests
    suite = unittest.TestLoader().loadTestsFromTestCase(TestCheckWattCollection)
    for test in suite:
        if 'async' in test._testMethodName or test._testMethodName.startswith('test_get'):
            # Wrap async tests
            original_method = getattr(test, test._testMethodName)
            setattr(test, test._testMethodName, lambda self, m=original_method: run_async_test(m(self)))

    unittest.main()
