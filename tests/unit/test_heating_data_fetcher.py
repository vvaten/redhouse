"""Unit tests for heating data fetcher."""

import datetime
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from src.control.heating_data_fetcher import HeatingDataFetcher


class TestHeatingDataFetcher(unittest.TestCase):
    """Test cases for HeatingDataFetcher class."""

    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def setUp(self, mock_config, mock_influx_class):
        """Set up test fixtures."""
        # Mock config
        self.mock_config = Mock()
        self.mock_config.influxdb_org = "test_org"
        self.mock_config.influxdb_bucket_emeters = "emeters_test"
        self.mock_config.influxdb_bucket_spotprice = "spotprice_test"
        self.mock_config.influxdb_bucket_weather = "weather_test"
        mock_config.return_value = self.mock_config

        # Mock InfluxClient
        self.mock_influx = Mock()
        mock_influx_class.return_value = self.mock_influx

        self.fetcher = HeatingDataFetcher()

    def test_initialization(self):
        """Test fetcher initialization."""
        self.assertIsNotNone(self.fetcher.config)
        self.assertIsNotNone(self.fetcher.influx)

    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_fetch_solar_predictions_success(self, mock_config, mock_influx_class):
        """Test successful fetch of solar predictions."""
        # Setup
        mock_config.return_value = self.mock_config
        mock_query_api = Mock()
        self.mock_influx.query_api = mock_query_api

        # Create mock records
        mock_record1 = Mock()
        mock_record1.get_time.return_value = datetime.datetime(
            2025, 1, 15, 12, 0, tzinfo=datetime.timezone.utc
        )
        mock_record1.get_value.return_value = 2.5

        mock_record2 = Mock()
        mock_record2.get_time.return_value = datetime.datetime(
            2025, 1, 15, 13, 0, tzinfo=datetime.timezone.utc
        )
        mock_record2.get_value.return_value = 3.0

        mock_table = Mock()
        mock_table.records = [mock_record1, mock_record2]
        mock_query_api.query.return_value = [mock_table]

        mock_influx_class.return_value = self.mock_influx

        fetcher = HeatingDataFetcher()
        result = fetcher._fetch_solar_predictions(start_offset=0, stop_offset=1)

        # Verify results
        self.assertEqual(len(result), 2)
        self.assertIn(datetime.datetime(2025, 1, 15, 12, 0, tzinfo=datetime.timezone.utc), result)
        self.assertIn(datetime.datetime(2025, 1, 15, 13, 0, tzinfo=datetime.timezone.utc), result)
        self.assertEqual(
            result[datetime.datetime(2025, 1, 15, 12, 0, tzinfo=datetime.timezone.utc)][
                "solar_yield_avg_prediction"
            ],
            2.5,
        )

    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_fetch_solar_predictions_empty(self, mock_config, mock_influx_class):
        """Test handling of empty solar predictions."""
        mock_config.return_value = self.mock_config
        mock_query_api = Mock()
        self.mock_influx.query_api = mock_query_api
        mock_query_api.query.return_value = []
        mock_influx_class.return_value = self.mock_influx

        fetcher = HeatingDataFetcher()
        result = fetcher._fetch_solar_predictions(start_offset=0, stop_offset=1)

        self.assertEqual(result, {})

    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_fetch_solar_predictions_exception(self, mock_config, mock_influx_class):
        """Test handling of exceptions in solar predictions fetch."""
        mock_config.return_value = self.mock_config
        mock_query_api = Mock()
        self.mock_influx.query_api = mock_query_api
        mock_query_api.query.side_effect = Exception("InfluxDB error")
        mock_influx_class.return_value = self.mock_influx

        fetcher = HeatingDataFetcher()
        result = fetcher._fetch_solar_predictions(start_offset=0, stop_offset=1)

        self.assertEqual(result, {})

    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_fetch_spot_prices_success(self, mock_config, mock_influx_class):
        """Test successful fetch of spot prices."""
        mock_config.return_value = self.mock_config
        mock_query_api = Mock()
        self.mock_influx.query_api = mock_query_api

        # Create mock records for price_total
        mock_record1 = Mock()
        mock_record1.get_time.return_value = datetime.datetime(
            2025, 1, 15, 12, 0, tzinfo=datetime.timezone.utc
        )
        mock_record1.get_field.return_value = "price_total"
        mock_record1.get_value.return_value = 10.5

        # Create mock records for price_sell
        mock_record2 = Mock()
        mock_record2.get_time.return_value = datetime.datetime(
            2025, 1, 15, 12, 0, tzinfo=datetime.timezone.utc
        )
        mock_record2.get_field.return_value = "price_sell"
        mock_record2.get_value.return_value = 5.0

        mock_table = Mock()
        mock_table.records = [mock_record1, mock_record2]
        mock_query_api.query.return_value = [mock_table]

        mock_influx_class.return_value = self.mock_influx

        fetcher = HeatingDataFetcher()
        result = fetcher._fetch_spot_prices(start_offset=0, stop_offset=1)

        # Verify results
        self.assertEqual(len(result), 1)
        timestamp = datetime.datetime(2025, 1, 15, 12, 0, tzinfo=datetime.timezone.utc)
        self.assertIn(timestamp, result)
        self.assertEqual(result[timestamp]["price_total"], 10.5)
        self.assertEqual(result[timestamp]["price_sell"], 5.0)

    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_fetch_spot_prices_empty(self, mock_config, mock_influx_class):
        """Test handling of empty spot prices."""
        mock_config.return_value = self.mock_config
        mock_query_api = Mock()
        self.mock_influx.query_api = mock_query_api
        mock_query_api.query.return_value = []
        mock_influx_class.return_value = self.mock_influx

        fetcher = HeatingDataFetcher()
        result = fetcher._fetch_spot_prices(start_offset=0, stop_offset=1)

        self.assertEqual(result, {})

    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_fetch_spot_prices_exception(self, mock_config, mock_influx_class):
        """Test handling of exceptions in spot prices fetch."""
        mock_config.return_value = self.mock_config
        mock_query_api = Mock()
        self.mock_influx.query_api = mock_query_api
        mock_query_api.query.side_effect = Exception("InfluxDB error")
        mock_influx_class.return_value = self.mock_influx

        fetcher = HeatingDataFetcher()
        result = fetcher._fetch_spot_prices(start_offset=0, stop_offset=1)

        self.assertEqual(result, {})

    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_fetch_weather_forecast_success(self, mock_config, mock_influx_class):
        """Test successful fetch of weather forecast."""
        mock_config.return_value = self.mock_config
        mock_query_api = Mock()
        self.mock_influx.query_api = mock_query_api

        # Create mock records
        mock_record1 = Mock()
        mock_record1.get_time.return_value = datetime.datetime(
            2025, 1, 15, 12, 0, tzinfo=datetime.timezone.utc
        )
        mock_record1.get_value.return_value = -5.0

        mock_record2 = Mock()
        mock_record2.get_time.return_value = datetime.datetime(
            2025, 1, 15, 13, 0, tzinfo=datetime.timezone.utc
        )
        mock_record2.get_value.return_value = -4.5

        mock_table = Mock()
        mock_table.records = [mock_record1, mock_record2]
        mock_query_api.query.return_value = [mock_table]

        mock_influx_class.return_value = self.mock_influx

        fetcher = HeatingDataFetcher()
        result = fetcher._fetch_weather_forecast(start_offset=0, stop_offset=1)

        # Verify results
        self.assertEqual(len(result), 2)
        timestamp1 = datetime.datetime(2025, 1, 15, 12, 0, tzinfo=datetime.timezone.utc)
        self.assertIn(timestamp1, result)
        self.assertEqual(result[timestamp1]["Air temperature"], -5.0)

    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_fetch_weather_forecast_empty(self, mock_config, mock_influx_class):
        """Test handling of empty weather forecast."""
        mock_config.return_value = self.mock_config
        mock_query_api = Mock()
        self.mock_influx.query_api = mock_query_api
        mock_query_api.query.return_value = []
        mock_influx_class.return_value = self.mock_influx

        fetcher = HeatingDataFetcher()
        result = fetcher._fetch_weather_forecast(start_offset=0, stop_offset=1)

        self.assertEqual(result, {})

    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_fetch_weather_forecast_exception(self, mock_config, mock_influx_class):
        """Test handling of exceptions in weather forecast fetch."""
        mock_config.return_value = self.mock_config
        mock_query_api = Mock()
        self.mock_influx.query_api = mock_query_api
        mock_query_api.query.side_effect = Exception("InfluxDB error")
        mock_influx_class.return_value = self.mock_influx

        fetcher = HeatingDataFetcher()
        result = fetcher._fetch_weather_forecast(start_offset=0, stop_offset=1)

        self.assertEqual(result, {})

    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_merge_data_all_sources(self, mock_config, mock_influx_class):
        """Test merging data from all sources."""
        mock_config.return_value = self.mock_config
        mock_influx_class.return_value = self.mock_influx

        fetcher = HeatingDataFetcher()

        timestamp = datetime.datetime(2025, 1, 15, 12, 0, tzinfo=datetime.timezone.utc)

        solar_data = {timestamp: {"solar_yield_avg_prediction": 2.5}}
        price_data = {timestamp: {"price_total": 10.5, "price_sell": 5.0}}
        weather_data = {timestamp: {"Air temperature": -5.0}}

        result = fetcher._merge_data(solar_data, price_data, weather_data)

        # Verify DataFrame structure
        self.assertIsInstance(result, pd.DataFrame)
        self.assertEqual(len(result), 1)
        self.assertIn("solar_yield_avg_prediction", result.columns)
        self.assertIn("price_total", result.columns)
        self.assertIn("price_sell", result.columns)
        self.assertIn("Air temperature", result.columns)
        self.assertIn("local_time", result.columns)
        self.assertIn("time_floor", result.columns)
        self.assertIn("time_floor_local", result.columns)

        # Verify values
        self.assertEqual(result.iloc[0]["solar_yield_avg_prediction"], 2.5)
        self.assertEqual(result.iloc[0]["price_total"], 10.5)
        self.assertEqual(result.iloc[0]["price_sell"], 5.0)
        self.assertEqual(result.iloc[0]["Air temperature"], -5.0)

    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_merge_data_partial_sources(self, mock_config, mock_influx_class):
        """Test merging data when some sources have partial data."""
        mock_config.return_value = self.mock_config
        mock_influx_class.return_value = self.mock_influx

        fetcher = HeatingDataFetcher()

        timestamp1 = datetime.datetime(2025, 1, 15, 12, 0, tzinfo=datetime.timezone.utc)
        timestamp2 = datetime.datetime(2025, 1, 15, 13, 0, tzinfo=datetime.timezone.utc)

        solar_data = {timestamp1: {"solar_yield_avg_prediction": 2.5}}
        price_data = {
            timestamp1: {"price_total": 10.5, "price_sell": 5.0},
            timestamp2: {"price_total": 11.0, "price_sell": 5.5},
        }
        weather_data = {timestamp1: {"Air temperature": -5.0}}

        result = fetcher._merge_data(solar_data, price_data, weather_data)

        # Verify we have both timestamps
        self.assertEqual(len(result), 2)

    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_merge_data_empty(self, mock_config, mock_influx_class):
        """Test merging when all sources are empty."""
        mock_config.return_value = self.mock_config
        mock_influx_class.return_value = self.mock_influx

        fetcher = HeatingDataFetcher()

        result = fetcher._merge_data({}, {}, {})

        # Verify empty DataFrame
        self.assertIsInstance(result, pd.DataFrame)
        self.assertTrue(result.empty)

    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_merge_data_sorted_by_timestamp(self, mock_config, mock_influx_class):
        """Test that merged data is sorted by timestamp."""
        mock_config.return_value = self.mock_config
        mock_influx_class.return_value = self.mock_influx

        fetcher = HeatingDataFetcher()

        timestamp1 = datetime.datetime(2025, 1, 15, 14, 0, tzinfo=datetime.timezone.utc)
        timestamp2 = datetime.datetime(2025, 1, 15, 12, 0, tzinfo=datetime.timezone.utc)
        timestamp3 = datetime.datetime(2025, 1, 15, 13, 0, tzinfo=datetime.timezone.utc)

        solar_data = {
            timestamp1: {"solar_yield_avg_prediction": 3.0},
            timestamp2: {"solar_yield_avg_prediction": 2.5},
            timestamp3: {"solar_yield_avg_prediction": 2.8},
        }

        result = fetcher._merge_data(solar_data, {}, {})

        # Verify sorting
        timestamps = result["index"].tolist()
        self.assertEqual(timestamps[0], timestamp2)
        self.assertEqual(timestamps[1], timestamp3)
        self.assertEqual(timestamps[2], timestamp1)

    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_merge_data_weather_only_timestamp(self, mock_config, mock_influx_class):
        """Test merging when weather has unique timestamps."""
        mock_config.return_value = self.mock_config
        mock_influx_class.return_value = self.mock_influx

        fetcher = HeatingDataFetcher()

        timestamp1 = datetime.datetime(2025, 1, 15, 12, 0, tzinfo=datetime.timezone.utc)
        timestamp2 = datetime.datetime(2025, 1, 15, 13, 0, tzinfo=datetime.timezone.utc)

        solar_data = {timestamp1: {"solar_yield_avg_prediction": 2.5}}
        price_data = {timestamp1: {"price_total": 10.5}}
        weather_data = {
            timestamp1: {"Air temperature": -5.0},
            timestamp2: {"Air temperature": -4.5},
        }

        result = fetcher._merge_data(solar_data, price_data, weather_data)

        # Verify we have both timestamps
        self.assertEqual(len(result), 2)
        # Verify weather data at unique timestamp
        self.assertEqual(result.iloc[1]["Air temperature"], -4.5)

    @patch("src.control.heating_data_fetcher.HeatingDataFetcher._fetch_solar_predictions")
    @patch("src.control.heating_data_fetcher.HeatingDataFetcher._fetch_spot_prices")
    @patch("src.control.heating_data_fetcher.HeatingDataFetcher._fetch_weather_forecast")
    @patch("src.control.heating_data_fetcher.HeatingDataFetcher._merge_data")
    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_fetch_heating_data_default_params(
        self,
        mock_config,
        mock_influx_class,
        mock_merge,
        mock_weather,
        mock_prices,
        mock_solar,
    ):
        """Test fetch_heating_data with default parameters."""
        mock_config.return_value = self.mock_config
        mock_influx_class.return_value = self.mock_influx

        # Setup mocks
        mock_solar.return_value = {}
        mock_prices.return_value = {}
        mock_weather.return_value = {}
        mock_merge.return_value = pd.DataFrame()

        fetcher = HeatingDataFetcher()
        fetcher.fetch_heating_data()

        # Verify default parameters are used
        mock_solar.assert_called_once_with(0, 3)
        mock_prices.assert_called_once_with(0, 3)
        mock_weather.assert_called_once_with(0, 3)
        mock_merge.assert_called_once()

    @patch("src.control.heating_data_fetcher.HeatingDataFetcher._fetch_solar_predictions")
    @patch("src.control.heating_data_fetcher.HeatingDataFetcher._fetch_spot_prices")
    @patch("src.control.heating_data_fetcher.HeatingDataFetcher._fetch_weather_forecast")
    @patch("src.control.heating_data_fetcher.HeatingDataFetcher._merge_data")
    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_fetch_heating_data_custom_params(
        self,
        mock_config,
        mock_influx_class,
        mock_merge,
        mock_weather,
        mock_prices,
        mock_solar,
    ):
        """Test fetch_heating_data with custom parameters."""
        mock_config.return_value = self.mock_config
        mock_influx_class.return_value = self.mock_influx

        # Setup mocks
        mock_solar.return_value = {}
        mock_prices.return_value = {}
        mock_weather.return_value = {}
        mock_merge.return_value = pd.DataFrame()

        fetcher = HeatingDataFetcher()
        fetcher.fetch_heating_data(date_offset=2, lookback_days=2, lookahead_days=3)

        # Verify custom parameters are used (offset=2, lookback=2, lookahead=3)
        # start_offset = 2 - 2 = 0, stop_offset = 2 + 3 = 5
        mock_solar.assert_called_once_with(0, 5)
        mock_prices.assert_called_once_with(0, 5)
        mock_weather.assert_called_once_with(0, 5)
        mock_merge.assert_called_once()

    @patch("src.control.heating_data_fetcher.datetime")
    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_get_day_average_temperature_success(
        self, mock_config, mock_influx_class, mock_datetime
    ):
        """Test getting average temperature for a specific day."""
        mock_config.return_value = self.mock_config
        mock_influx_class.return_value = self.mock_influx

        # Mock datetime.datetime.now()
        mock_now = datetime.datetime(2025, 1, 15, 10, 0, 0)
        mock_datetime.datetime.now.return_value = mock_now
        mock_datetime.timedelta = datetime.timedelta

        fetcher = HeatingDataFetcher()

        # Create sample DataFrame
        timestamps = pd.date_range(
            start="2025-01-16 00:00:00",
            periods=48,
            freq="H",
            tz="Europe/Helsinki",
        )

        df = pd.DataFrame(
            {
                "time_floor_local": timestamps,
                "Air temperature": [-5.0] * 24 + [-3.0] * 24,
            }
        )

        result = fetcher.get_day_average_temperature(df, date_offset=1)

        # Verify result
        self.assertIsInstance(result, float)
        self.assertAlmostEqual(result, -5.0, places=1)

    @patch("src.control.heating_data_fetcher.datetime")
    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_get_day_average_temperature_no_data(
        self, mock_config, mock_influx_class, mock_datetime
    ):
        """Test getting average temperature when no data available."""
        mock_config.return_value = self.mock_config
        mock_influx_class.return_value = self.mock_influx

        mock_now = datetime.datetime(2025, 1, 15, 10, 0, 0)
        mock_datetime.datetime.now.return_value = mock_now
        mock_datetime.timedelta = datetime.timedelta

        fetcher = HeatingDataFetcher()

        # Create DataFrame with columns but no data
        df = pd.DataFrame(columns=["time_floor_local", "Air temperature"])

        result = fetcher.get_day_average_temperature(df, date_offset=1)

        # Verify returns 0.0 for missing data
        self.assertEqual(result, 0.0)

    @patch("src.control.heating_data_fetcher.datetime")
    @patch("src.control.heating_data_fetcher.InfluxClient")
    @patch("src.control.heating_data_fetcher.get_config")
    def test_get_day_average_temperature_missing_column(
        self, mock_config, mock_influx_class, mock_datetime
    ):
        """Test getting average temperature when column is missing."""
        mock_config.return_value = self.mock_config
        mock_influx_class.return_value = self.mock_influx

        mock_now = datetime.datetime(2025, 1, 15, 10, 0, 0)
        mock_datetime.datetime.now.return_value = mock_now
        mock_datetime.timedelta = datetime.timedelta

        fetcher = HeatingDataFetcher()

        # Create DataFrame without Air temperature column
        timestamps = pd.date_range(
            start="2025-01-16 00:00:00",
            periods=24,
            freq="H",
            tz="Europe/Helsinki",
        )

        df = pd.DataFrame(
            {
                "time_floor_local": timestamps,
                "price_total": [10.0] * 24,
            }
        )

        result = fetcher.get_day_average_temperature(df, date_offset=1)

        # Verify returns 0.0 for missing column
        self.assertEqual(result, 0.0)


if __name__ == "__main__":
    unittest.main()
