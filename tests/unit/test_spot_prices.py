"""Unit tests for spot price data collection."""

import unittest
from unittest.mock import Mock, patch, AsyncMock, mock_open
import datetime
import json

from src.data_collection.spot_prices import (
    fetch_spot_prices_from_api,
    process_spot_prices,
    save_spot_prices_to_file,
    load_status,
    save_status,
    SPOT_PRICE_API_URL
)


class TestSpotPriceCollection(unittest.TestCase):
    """Test spot price collection functions."""

    @patch('aiohttp.ClientSession')
    async def test_fetch_spot_prices_success(self, mock_session_class):
        """Test successful spot price fetch from API."""
        # Mock API response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value=json.dumps([
            {
                "DateTime": "2025-10-18T00:00:00+03:00",
                "PriceNoTax": 0.10585,
                "PriceWithTax": 0.13284
            },
            {
                "DateTime": "2025-10-18T00:15:00+03:00",
                "PriceNoTax": 0.08107,
                "PriceWithTax": 0.10174
            }
        ]))

        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.get.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value = mock_session

        result = await fetch_spot_prices_from_api()

        # Verify results
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['PriceNoTax'], 0.10585)
        self.assertEqual(result[1]['DateTime'], "2025-10-18T00:15:00+03:00")

    @patch('aiohttp.ClientSession')
    async def test_fetch_spot_prices_http_error(self, mock_session_class):
        """Test handling of HTTP errors."""
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Server error")

        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.get.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value = mock_session

        result = await fetch_spot_prices_from_api()

        self.assertIsNone(result)

    def test_process_spot_prices_success(self):
        """Test processing of spot price data."""
        # Mock config
        config = Mock()
        config.get = lambda key: {
            'spot_value_added_tax': 1.24,
            'spot_sellers_margin': 0.50,
            'spot_production_buyback_margin': 0.30,
            'spot_transfer_day_price': 2.59,
            'spot_transfer_night_price': 1.35,
            'spot_transfer_tax_price': 2.79372
        }.get(key)

        raw_prices = [
            {
                "DateTime": "2025-10-18T14:00:00+03:00",  # Daytime (14:00)
                "PriceNoTax": 10.0
            },
            {
                "DateTime": "2025-10-18T23:00:00+03:00",  # Nighttime (23:00)
                "PriceNoTax": 5.0
            }
        ]

        result = process_spot_prices(raw_prices, config)

        # Verify results
        self.assertEqual(len(result), 2)

        # Check first entry (daytime)
        self.assertEqual(result[0]['price'], 10.0)
        self.assertEqual(result[0]['price_withtax'], 12.4)  # 10.0 * 1.24
        self.assertIn('epoch_timestamp', result[0])
        self.assertIn('datetime_utc', result[0])

        # Check price_total calculation includes day transfer price
        # price_total = price_withtax + 0.01 * (margins + transfer + tax)
        # = 12.4 + 0.01 * (0.50 + 2.59 + 2.79372)
        expected_total_day = round(12.4 + 0.01 * (0.50 + 2.59 + 2.79372), 6)
        self.assertEqual(result[0]['price_total'], expected_total_day)

        # Check second entry (nighttime) uses night transfer price
        expected_total_night = round(6.2 + 0.01 * (0.50 + 1.35 + 2.79372), 6)
        self.assertEqual(result[1]['price_total'], expected_total_night)

    def test_process_spot_prices_missing_config(self):
        """Test that missing config parameters raise error."""
        config = Mock()
        config.get = lambda key: None  # All parameters missing

        raw_prices = [{"DateTime": "2025-10-18T14:00:00+03:00", "PriceNoTax": 10.0}]

        with self.assertRaises(ValueError) as ctx:
            process_spot_prices(raw_prices, config)

        self.assertIn("Missing required config", str(ctx.exception))

    def test_process_spot_prices_quarter_hourly(self):
        """Test processing of 15-minute interval prices."""
        config = Mock()
        config.get = lambda key: {
            'spot_value_added_tax': 1.24,
            'spot_sellers_margin': 0.50,
            'spot_production_buyback_margin': 0.30,
            'spot_transfer_day_price': 2.59,
            'spot_transfer_night_price': 1.35,
            'spot_transfer_tax_price': 2.79372
        }.get(key)

        # Test all 4 quarters of an hour
        raw_prices = [
            {"DateTime": "2025-10-18T14:00:00+03:00", "PriceNoTax": 10.0},
            {"DateTime": "2025-10-18T14:15:00+03:00", "PriceNoTax": 10.5},
            {"DateTime": "2025-10-18T14:30:00+03:00", "PriceNoTax": 11.0},
            {"DateTime": "2025-10-18T14:45:00+03:00", "PriceNoTax": 11.5},
        ]

        result = process_spot_prices(raw_prices, config)

        # All 4 entries should be processed
        self.assertEqual(len(result), 4)

        # Verify timestamps are 15 minutes apart
        timestamps = [entry['epoch_timestamp'] for entry in result]
        self.assertEqual(timestamps[1] - timestamps[0], 900)  # 15 minutes
        self.assertEqual(timestamps[2] - timestamps[1], 900)
        self.assertEqual(timestamps[3] - timestamps[2], 900)

    @patch('builtins.open', new_callable=mock_open)
    def test_save_spot_prices_to_file(self, mock_file):
        """Test saving spot prices to file."""
        raw_prices = [
            {"DateTime": "2025-10-18T14:00:00+03:00", "PriceNoTax": 10.0}
        ]

        result = save_spot_prices_to_file(raw_prices, 'test.json')

        self.assertTrue(result)
        mock_file.assert_called_once_with('test.json', 'w')

    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data='{"latest_epoch_timestamp": 1697616000}')
    def test_load_status_existing(self, mock_file, mock_exists):
        """Test loading existing status file."""
        mock_exists.return_value = True

        result = load_status()

        self.assertEqual(result['latest_epoch_timestamp'], 1697616000)

    @patch('os.path.exists')
    def test_load_status_missing(self, mock_exists):
        """Test loading when status file doesn't exist."""
        mock_exists.return_value = False

        result = load_status()

        self.assertEqual(result['latest_epoch_timestamp'], 0)

    @patch('builtins.open', new_callable=mock_open)
    def test_save_status(self, mock_file):
        """Test saving status file."""
        result = save_status(1697616000)

        self.assertTrue(result)
        mock_file.assert_called_once()


# Async test runner
def run_async_test(coro):
    """Helper to run async tests."""
    import asyncio
    return asyncio.run(coro)


if __name__ == '__main__':
    # Run async tests
    suite = unittest.TestLoader().loadTestsFromTestCase(TestSpotPriceCollection)
    for test in suite:
        if 'async' in test._testMethodName or test._testMethodName.startswith('test_fetch'):
            # Wrap async tests
            original_method = getattr(test, test._testMethodName)
            setattr(test, test._testMethodName, lambda self, m=original_method: run_async_test(m(self)))

    unittest.main()
