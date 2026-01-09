"""Unit tests for spot price data collection."""

import datetime
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, mock_open, patch

import pytest

from src.data_collection.spot_prices import (
    collect_spot_prices,
    fetch_spot_prices_from_api,
    load_status,
    process_spot_prices,
    save_spot_prices_to_file,
    save_status,
    write_spot_prices_to_influx,
)


class TestSpotPriceCollection(unittest.TestCase):
    """Test spot price collection functions."""

    def setUp(self):
        """Set up test fixtures."""
        self.sample_raw_prices = [
            {"DateTime": "2025-10-18T00:00:00+03:00", "PriceNoTax": 10.0},
            {"DateTime": "2025-10-18T01:00:00+03:00", "PriceNoTax": 8.5},
            {"DateTime": "2025-10-18T23:00:00+03:00", "PriceNoTax": 5.0},
        ]

        self.mock_config = Mock()
        self.mock_config.get = lambda key: {
            "spot_value_added_tax": 1.24,
            "spot_sellers_margin": 0.50,
            "spot_production_buyback_margin": 0.30,
            "spot_transfer_day_price": 2.59,
            "spot_transfer_night_price": 1.35,
            "spot_transfer_tax_price": 2.79372,
        }.get(key)
        self.mock_config.influxdb_bucket_spotprice = "spotprice_test"

    def test_process_spot_prices_success(self):
        """Test processing of spot price data."""
        raw_prices = [
            {"DateTime": "2025-10-18T14:00:00+03:00", "PriceNoTax": 10.0},
            {"DateTime": "2025-10-18T23:00:00+03:00", "PriceNoTax": 5.0},
        ]

        result = process_spot_prices(raw_prices, self.mock_config)

        # Verify results
        self.assertEqual(len(result), 2)

        # Check first entry (daytime)
        self.assertEqual(result[0]["price"], 10.0)
        self.assertEqual(result[0]["price_withtax"], 12.4)
        self.assertIn("epoch_timestamp", result[0])
        self.assertIn("datetime_utc", result[0])
        self.assertIn("datetime_local", result[0])
        self.assertIn("price_sell", result[0])

        # Verify price_sell calculation
        expected_sell = round(10.0 - 0.01 * 0.30, 4)
        self.assertEqual(result[0]["price_sell"], expected_sell)

    def test_process_spot_prices_missing_config(self):
        """Test that missing config parameters raise error."""
        config = Mock()
        config.get = lambda key: None

        raw_prices = [{"DateTime": "2025-10-18T14:00:00+03:00", "PriceNoTax": 10.0}]

        with self.assertRaises(ValueError) as ctx:
            process_spot_prices(raw_prices, config)

        self.assertIn("Missing required config", str(ctx.exception))

    def test_process_spot_prices_quarter_hourly(self):
        """Test processing of 15-minute interval prices."""
        raw_prices = [
            {"DateTime": "2025-10-18T14:00:00+03:00", "PriceNoTax": 10.0},
            {"DateTime": "2025-10-18T14:15:00+03:00", "PriceNoTax": 10.5},
            {"DateTime": "2025-10-18T14:30:00+03:00", "PriceNoTax": 11.0},
            {"DateTime": "2025-10-18T14:45:00+03:00", "PriceNoTax": 11.5},
        ]

        result = process_spot_prices(raw_prices, self.mock_config)

        self.assertEqual(len(result), 4)

        # Verify timestamps are 15 minutes apart
        timestamps = [entry["epoch_timestamp"] for entry in result]
        self.assertEqual(timestamps[1] - timestamps[0], 900)
        self.assertEqual(timestamps[2] - timestamps[1], 900)
        self.assertEqual(timestamps[3] - timestamps[2], 900)

    def test_process_spot_prices_night_tariff(self):
        """Test that night tariff (22:00-07:00) is applied correctly."""
        raw_prices = [
            {"DateTime": "2025-10-18T06:00:00+03:00", "PriceNoTax": 5.0},  # Night
            {"DateTime": "2025-10-18T07:00:00+03:00", "PriceNoTax": 6.0},  # Day
            {"DateTime": "2025-10-18T21:59:00+03:00", "PriceNoTax": 7.0},  # Day
            {"DateTime": "2025-10-18T22:00:00+03:00", "PriceNoTax": 8.0},  # Night
            {"DateTime": "2025-10-18T23:00:00+03:00", "PriceNoTax": 9.0},  # Night
        ]

        result = process_spot_prices(raw_prices, self.mock_config)

        # Night tariff: hours 22-23 and 0-6 (before 7)
        # Day tariff: hours 7-21
        night_hours = [0, 3, 4]  # indices for 06:00, 22:00, 23:00
        day_hours = [1, 2]  # indices for 07:00, 21:59

        for i in night_hours:
            # Night rate includes lower transfer price
            price_withtax = result[i]["price_withtax"]
            expected_total = round(price_withtax + 0.01 * (0.50 + 1.35 + 2.79372), 6)
            self.assertEqual(result[i]["price_total"], expected_total)

        for i in day_hours:
            # Day rate includes higher transfer price
            price_withtax = result[i]["price_withtax"]
            expected_total = round(price_withtax + 0.01 * (0.50 + 2.59 + 2.79372), 6)
            self.assertEqual(result[i]["price_total"], expected_total)

    def test_process_spot_prices_dst_transition(self):
        """Test DST transition handling for 2022-10-30."""
        raw_prices = [
            {"DateTime": "2022-10-30T03:00:00+02:00", "PriceNoTax": 10.0},
            {"DateTime": "2022-10-30T04:00:00+02:00", "PriceNoTax": 11.0},
        ]

        result = process_spot_prices(raw_prices, self.mock_config)

        # First entry has special offset
        # Second entry should be 3600 seconds later (1 hour) plus the DST offset
        self.assertEqual(len(result), 2)
        self.assertIn("epoch_timestamp", result[0])

    def test_process_spot_prices_error_handling(self):
        """Test that processing continues on individual entry errors."""
        raw_prices = [
            {"DateTime": "2025-10-18T14:00:00+03:00", "PriceNoTax": 10.0},  # Valid
            {"DateTime": "invalid", "PriceNoTax": 10.0},  # Invalid datetime
            {"DateTime": "2025-10-18T15:00:00+03:00", "PriceNoTax": 11.0},  # Valid
        ]

        result = process_spot_prices(raw_prices, self.mock_config)

        # Should have 2 valid entries (invalid one skipped)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["price"], 10.0)
        self.assertEqual(result[1]["price"], 11.0)

    @patch("builtins.open", new_callable=mock_open)
    def test_save_spot_prices_to_file(self, mock_file):
        """Test saving spot prices to file."""
        raw_prices = [{"DateTime": "2025-10-18T14:00:00+03:00", "PriceNoTax": 10.0}]

        result = save_spot_prices_to_file(raw_prices, "test.json")

        self.assertTrue(result)
        mock_file.assert_called_once_with("test.json", "w")

    @patch("builtins.open", side_effect=OSError("Write error"))
    def test_save_spot_prices_to_file_error(self, mock_file):
        """Test handling of file write errors."""
        raw_prices = [{"DateTime": "2025-10-18T14:00:00+03:00", "PriceNoTax": 10.0}]

        result = save_spot_prices_to_file(raw_prices, "test.json")

        self.assertFalse(result)

    @patch("os.path.exists")
    @patch(
        "builtins.open", new_callable=mock_open, read_data='{"latest_epoch_timestamp": 1697616000}'
    )
    def test_load_status_existing(self, mock_file, mock_exists):
        """Test loading existing status file."""
        mock_exists.return_value = True

        result = load_status()

        self.assertEqual(result["latest_epoch_timestamp"], 1697616000)

    @patch("os.path.exists")
    def test_load_status_missing(self, mock_exists):
        """Test loading when status file doesn't exist."""
        mock_exists.return_value = False

        result = load_status()

        self.assertEqual(result["latest_epoch_timestamp"], 0)

    @patch("os.path.exists")
    @patch("builtins.open", side_effect=OSError("Read error"))
    def test_load_status_error(self, mock_file, mock_exists):
        """Test handling of file read errors."""
        mock_exists.return_value = True

        result = load_status()

        # Should return default on error
        self.assertEqual(result["latest_epoch_timestamp"], 0)

    @patch("builtins.open", new_callable=mock_open)
    def test_save_status(self, mock_file):
        """Test saving status file."""
        result = save_status(1697616000)

        self.assertTrue(result)
        mock_file.assert_called_once()

    @patch("builtins.open", side_effect=OSError("Write error"))
    def test_save_status_error(self, mock_file):
        """Test handling of status file write errors."""
        result = save_status(1697616000)

        self.assertFalse(result)


# Async tests using pytest
@pytest.mark.asyncio
async def test_fetch_spot_prices_success():
    """Test successful spot price fetch from API."""
    sample_data = [
        {"DateTime": "2025-10-18T00:00:00+03:00", "PriceNoTax": 10.0},
        {"DateTime": "2025-10-18T01:00:00+03:00", "PriceNoTax": 8.5},
    ]

    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value=json.dumps(sample_data))

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value.__aenter__.return_value = mock_session

        result = await fetch_spot_prices_from_api()

        assert result is not None
        assert len(result) == 2
        assert result[0]["PriceNoTax"] == 10.0


@pytest.mark.asyncio
async def test_fetch_spot_prices_http_error():
    """Test handling of HTTP errors."""
    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Server error")

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value.__aenter__.return_value = mock_session

        result = await fetch_spot_prices_from_api()

        assert result is None


@pytest.mark.asyncio
async def test_fetch_spot_prices_exception():
    """Test handling of general exceptions."""
    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("Network error")
        mock_session_class.return_value.__aenter__.return_value = mock_session

        result = await fetch_spot_prices_from_api()

        assert result is None


@pytest.mark.asyncio
async def test_write_spot_prices_to_influx_success():
    """Test successful write to InfluxDB."""
    processed_prices = [
        {
            "epoch_timestamp": 1697616000,
            "datetime_utc": "2023-10-18T08:00:00+00:00",
            "price": 10.0,
            "price_total": 15.5,
        },
        {
            "epoch_timestamp": 1697619600,
            "datetime_utc": "2023-10-18T09:00:00+00:00",
            "price": 11.0,
            "price_total": 16.5,
        },
    ]

    with patch("src.data_collection.spot_prices.get_config") as mock_get_config:
        with patch("src.data_collection.spot_prices.InfluxClient") as mock_influx_class:
            mock_config = MagicMock()
            mock_config.influxdb_bucket_spotprice = "spotprice_test"
            mock_get_config.return_value = mock_config

            mock_influx = MagicMock()
            mock_influx.write_spot_prices.return_value = True
            mock_influx_class.return_value = mock_influx

            result = await write_spot_prices_to_influx(processed_prices, dry_run=False)

            assert result == 1697619600  # Latest timestamp
            mock_influx.write_spot_prices.assert_called_once()


@pytest.mark.asyncio
async def test_write_spot_prices_to_influx_empty():
    """Test write with empty data."""
    result = await write_spot_prices_to_influx([], dry_run=False)

    assert result is None


@pytest.mark.asyncio
async def test_write_spot_prices_to_influx_dry_run():
    """Test dry-run mode."""
    processed_prices = [
        {
            "epoch_timestamp": 1697616000,
            "datetime_utc": "2023-10-18T08:00:00+00:00",
            "price_total": 15.5,
        },
        {
            "epoch_timestamp": 1697619600,
            "datetime_utc": "2023-10-18T09:00:00+00:00",
            "price_total": 16.5,
        },
    ]

    with patch("src.data_collection.spot_prices.get_config") as mock_get_config:
        mock_config = MagicMock()
        mock_config.influxdb_bucket_spotprice = "spotprice_test"
        mock_get_config.return_value = mock_config

        result = await write_spot_prices_to_influx(processed_prices, dry_run=True)

        # Should return latest timestamp without writing
        assert result == 1697619600


@pytest.mark.asyncio
async def test_write_spot_prices_to_influx_failure():
    """Test handling of write failures."""
    processed_prices = [{"epoch_timestamp": 1697616000, "price_total": 15.5}]

    with patch("src.data_collection.spot_prices.get_config") as mock_get_config:
        with patch("src.data_collection.spot_prices.InfluxClient") as mock_influx_class:
            mock_config = MagicMock()
            mock_get_config.return_value = mock_config

            mock_influx = MagicMock()
            mock_influx.write_spot_prices.return_value = False
            mock_influx_class.return_value = mock_influx

            result = await write_spot_prices_to_influx(processed_prices, dry_run=False)

            assert result is None


@pytest.mark.asyncio
async def test_write_spot_prices_to_influx_exception():
    """Test handling of exceptions during write."""
    processed_prices = [{"epoch_timestamp": 1697616000, "price_total": 15.5}]

    with patch("src.data_collection.spot_prices.get_config") as mock_get_config:
        with patch("src.data_collection.spot_prices.InfluxClient") as mock_influx_class:
            mock_config = MagicMock()
            mock_get_config.return_value = mock_config

            mock_influx_class.side_effect = Exception("Connection error")

            result = await write_spot_prices_to_influx(processed_prices, dry_run=False)

            assert result is None


@pytest.mark.asyncio
async def test_collect_spot_prices_success():
    """Test successful full collection cycle."""
    sample_raw = [{"DateTime": "2025-10-18T14:00:00+03:00", "PriceNoTax": 10.0}]

    with patch("src.data_collection.spot_prices.get_config") as mock_get_config:
        with patch("src.data_collection.spot_prices.load_status") as mock_load_status:
            with patch("src.data_collection.spot_prices.fetch_spot_prices_from_api") as mock_fetch:
                with patch(
                    "src.data_collection.spot_prices.save_spot_prices_to_file"
                ) as mock_save_file:
                    with patch(
                        "src.data_collection.spot_prices.JSONDataLogger"
                    ) as mock_json_logger_class:
                        with patch(
                            "src.data_collection.spot_prices.write_spot_prices_to_influx"
                        ) as mock_write:
                            with patch(
                                "src.data_collection.spot_prices.save_status"
                            ) as mock_save_status:
                                # Setup mocks
                                mock_config = MagicMock()
                                mock_config.get = lambda key: {
                                    "spot_value_added_tax": 1.24,
                                    "spot_sellers_margin": 0.50,
                                    "spot_production_buyback_margin": 0.30,
                                    "spot_transfer_day_price": 2.59,
                                    "spot_transfer_night_price": 1.35,
                                    "spot_transfer_tax_price": 2.79372,
                                }.get(key)
                                mock_get_config.return_value = mock_config

                                mock_load_status.return_value = {"latest_epoch_timestamp": 0}
                                mock_fetch.return_value = sample_raw

                                mock_json_logger = MagicMock()
                                mock_json_logger_class.return_value = mock_json_logger

                                # Latest timestamp is tomorrow
                                future_timestamp = (
                                    int(datetime.datetime.utcnow().timestamp()) + 100000
                                )
                                mock_write.return_value = future_timestamp

                                result = await collect_spot_prices(dry_run=False)

                                assert result == 0
                                mock_fetch.assert_called_once()
                                mock_save_file.assert_called_once()
                                mock_write.assert_called_once()
                                mock_save_status.assert_called_once()


@pytest.mark.asyncio
async def test_collect_spot_prices_already_have_tomorrow():
    """Test that collection skips if we already have tomorrow's prices."""
    with patch("src.data_collection.spot_prices.get_config") as mock_get_config:
        with patch("src.data_collection.spot_prices.load_status") as mock_load_status:
            mock_config = MagicMock()
            mock_get_config.return_value = mock_config

            # Latest price is well into the future
            future_timestamp = int(datetime.datetime.utcnow().timestamp()) + 200000
            mock_load_status.return_value = {"latest_epoch_timestamp": future_timestamp}

            result = await collect_spot_prices(dry_run=False)

            # Should skip with success
            assert result == 0


@pytest.mark.asyncio
async def test_collect_spot_prices_fetch_fails():
    """Test handling when API fetch fails."""
    with patch("src.data_collection.spot_prices.get_config") as mock_get_config:
        with patch("src.data_collection.spot_prices.load_status") as mock_load_status:
            with patch("src.data_collection.spot_prices.fetch_spot_prices_from_api") as mock_fetch:
                mock_config = MagicMock()
                mock_get_config.return_value = mock_config

                mock_load_status.return_value = {"latest_epoch_timestamp": 0}
                mock_fetch.return_value = None

                result = await collect_spot_prices(dry_run=False)

                assert result == 1


@pytest.mark.asyncio
async def test_collect_spot_prices_no_processed_data():
    """Test handling when processing yields no data."""
    sample_raw = [{"DateTime": "invalid", "PriceNoTax": 10.0}]  # Will fail processing

    with patch("src.data_collection.spot_prices.get_config") as mock_get_config:
        with patch("src.data_collection.spot_prices.load_status") as mock_load_status:
            with patch("src.data_collection.spot_prices.fetch_spot_prices_from_api") as mock_fetch:
                with patch("src.data_collection.spot_prices.save_spot_prices_to_file"):
                    with patch("src.data_collection.spot_prices.JSONDataLogger"):
                        mock_config = MagicMock()
                        mock_config.get = lambda key: {
                            "spot_value_added_tax": 1.24,
                            "spot_sellers_margin": 0.50,
                            "spot_production_buyback_margin": 0.30,
                            "spot_transfer_day_price": 2.59,
                            "spot_transfer_night_price": 1.35,
                            "spot_transfer_tax_price": 2.79372,
                        }.get(key)
                        mock_get_config.return_value = mock_config

                        mock_load_status.return_value = {"latest_epoch_timestamp": 0}
                        mock_fetch.return_value = sample_raw

                        # Processing will fail due to invalid datetime, yielding no data
                        result = await collect_spot_prices(dry_run=False)

                        assert result == 1


@pytest.mark.asyncio
async def test_collect_spot_prices_write_fails():
    """Test handling when InfluxDB write fails."""
    sample_raw = [{"DateTime": "2025-10-18T14:00:00+03:00", "PriceNoTax": 10.0}]

    with patch("src.data_collection.spot_prices.get_config") as mock_get_config:
        with patch("src.data_collection.spot_prices.load_status") as mock_load_status:
            with patch("src.data_collection.spot_prices.fetch_spot_prices_from_api") as mock_fetch:
                with patch("src.data_collection.spot_prices.save_spot_prices_to_file"):
                    with patch("src.data_collection.spot_prices.JSONDataLogger"):
                        with patch(
                            "src.data_collection.spot_prices.write_spot_prices_to_influx"
                        ) as mock_write:
                            mock_config = MagicMock()
                            mock_config.get = lambda key: {
                                "spot_value_added_tax": 1.24,
                                "spot_sellers_margin": 0.50,
                                "spot_production_buyback_margin": 0.30,
                                "spot_transfer_day_price": 2.59,
                                "spot_transfer_night_price": 1.35,
                                "spot_transfer_tax_price": 2.79372,
                            }.get(key)
                            mock_get_config.return_value = mock_config

                            mock_load_status.return_value = {"latest_epoch_timestamp": 0}
                            mock_fetch.return_value = sample_raw
                            mock_write.return_value = None  # Write failed

                            result = await collect_spot_prices(dry_run=False)

                            assert result == 1


@pytest.mark.asyncio
async def test_collect_spot_prices_dry_run():
    """Test collection in dry-run mode."""
    sample_raw = [{"DateTime": "2025-10-18T14:00:00+03:00", "PriceNoTax": 10.0}]

    with patch("src.data_collection.spot_prices.get_config") as mock_get_config:
        with patch("src.data_collection.spot_prices.load_status") as mock_load_status:
            with patch("src.data_collection.spot_prices.fetch_spot_prices_from_api") as mock_fetch:
                with patch("src.data_collection.spot_prices.save_spot_prices_to_file"):
                    with patch("src.data_collection.spot_prices.JSONDataLogger"):
                        with patch(
                            "src.data_collection.spot_prices.write_spot_prices_to_influx"
                        ) as mock_write:
                            with patch(
                                "src.data_collection.spot_prices.save_status"
                            ) as mock_save_status:
                                mock_config = MagicMock()
                                mock_config.get = lambda key: {
                                    "spot_value_added_tax": 1.24,
                                    "spot_sellers_margin": 0.50,
                                    "spot_production_buyback_margin": 0.30,
                                    "spot_transfer_day_price": 2.59,
                                    "spot_transfer_night_price": 1.35,
                                    "spot_transfer_tax_price": 2.79372,
                                }.get(key)
                                mock_get_config.return_value = mock_config

                                mock_load_status.return_value = {"latest_epoch_timestamp": 0}
                                mock_fetch.return_value = sample_raw

                                future_timestamp = (
                                    int(datetime.datetime.utcnow().timestamp()) + 100000
                                )
                                mock_write.return_value = future_timestamp

                                result = await collect_spot_prices(dry_run=True)

                                assert result == 0
                                # Should NOT save status in dry-run
                                mock_save_status.assert_not_called()


def test_main_dry_run():
    """Test main entry point with dry-run."""
    with patch("asyncio.run") as mock_asyncio_run:
        with patch("argparse.ArgumentParser.parse_args") as mock_parse_args:
            from src.data_collection.spot_prices import main

            mock_args = MagicMock()
            mock_args.dry_run = True
            mock_args.verbose = False
            mock_parse_args.return_value = mock_args

            mock_asyncio_run.return_value = 0

            result = main()

            assert result == 0
            mock_asyncio_run.assert_called_once()


def test_main_verbose():
    """Test main entry point with verbose logging."""
    with patch("asyncio.run") as mock_asyncio_run:
        with patch("argparse.ArgumentParser.parse_args") as mock_parse_args:
            from src.data_collection.spot_prices import main

            mock_args = MagicMock()
            mock_args.dry_run = False
            mock_args.verbose = True
            mock_parse_args.return_value = mock_args

            mock_asyncio_run.return_value = 0

            result = main()

            assert result == 0


def test_main_exception():
    """Test main entry point exception handling."""
    with patch("asyncio.run") as mock_asyncio_run:
        with patch("argparse.ArgumentParser.parse_args") as mock_parse_args:
            from src.data_collection.spot_prices import main

            mock_args = MagicMock()
            mock_args.dry_run = False
            mock_args.verbose = False
            mock_parse_args.return_value = mock_args

            mock_asyncio_run.side_effect = Exception("Test error")

            result = main()

            assert result == 1


if __name__ == "__main__":
    unittest.main()
