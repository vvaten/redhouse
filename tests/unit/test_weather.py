"""Unit tests for weather data collection."""

import datetime
import unittest
from unittest.mock import MagicMock, Mock, patch

from src.data_collection.weather import (
    EXCLUDED_FIELDS,
    collect_weather,
    fetch_weather_forecast,
    main,
    save_weather_to_file,
    write_weather_to_influx,
)


class TestWeatherCollection(unittest.TestCase):
    """Test weather collection functions."""

    def test_excluded_fields_no_unicode(self):
        """Verify excluded fields contain no unicode characters."""
        for field in EXCLUDED_FIELDS:
            self.assertTrue(field.isascii(), f"Field {field} contains non-ASCII characters")

    @patch("src.data_collection.weather.download_stored_query")
    def test_fetch_weather_forecast_success(self, mock_download):
        """Test successful weather forecast fetch."""
        # Mock weather data
        mock_weather = Mock()
        timestamp1 = datetime.datetime(2025, 10, 18, 12, 0)
        timestamp2 = datetime.datetime(2025, 10, 18, 12, 15)

        mock_weather.data = {
            timestamp1: {
                "0": {
                    "Temperature": {"value": 15.5},
                    "Wind speed": {"value": 3.2},
                    "Geopotential height": {"value": 100},  # Should be excluded
                }
            },
            timestamp2: {"0": {"Temperature": {"value": 15.8}, "Wind speed": {"value": 3.5}}},
        }

        mock_download.return_value = mock_weather

        result = fetch_weather_forecast("60.1699,24.9384")

        # Verify results
        self.assertEqual(len(result), 2)
        self.assertIn(timestamp1, result)
        self.assertIn(timestamp2, result)

        # Check first timestamp
        self.assertEqual(result[timestamp1]["Temperature"], 15.5)
        self.assertEqual(result[timestamp1]["Wind speed"], 3.2)
        self.assertNotIn("Geopotential height", result[timestamp1])  # Excluded

        # Check second timestamp
        self.assertEqual(result[timestamp2]["Temperature"], 15.8)

    @patch("src.data_collection.weather.download_stored_query")
    def test_fetch_weather_forecast_empty(self, mock_download):
        """Test handling of empty weather data."""
        mock_weather = Mock()
        mock_weather.data = {}
        mock_download.return_value = mock_weather

        result = fetch_weather_forecast("60.1699,24.9384")

        self.assertEqual(result, {})

    @patch("src.data_collection.weather.download_stored_query")
    def test_fetch_weather_forecast_exception(self, mock_download):
        """Test handling of API exceptions."""
        mock_download.side_effect = Exception("API error")

        result = fetch_weather_forecast("60.1699,24.9384")

        self.assertEqual(result, {})

    @patch("builtins.open", create=True)
    @patch("os.makedirs")
    @patch("json.dump")
    def test_save_weather_to_file(self, mock_json_dump, mock_makedirs, mock_open):
        """Test saving weather data to file."""
        weather_data = {
            datetime.datetime(2025, 10, 18, 12, 0): {"Temperature": 15.5, "Wind speed": 3.2}
        }

        result = save_weather_to_file(weather_data, base_dir="/tmp/test")

        # Verify directory creation
        mock_makedirs.assert_called_once()

        # Verify file was opened for writing
        mock_open.assert_called_once()

        # Verify json.dump was called
        mock_json_dump.assert_called_once()

        # Result should be a filepath
        self.assertIsNotNone(result)
        self.assertIn("weather_data", result)

    @patch("src.data_collection.weather.get_config")
    @patch("src.data_collection.weather.InfluxClient")
    def test_write_weather_to_influx_dry_run(self, mock_influx_class, mock_config):
        """Test dry-run mode doesn't write to InfluxDB."""
        # Mock config for dry-run logging
        mock_config_obj = Mock()
        mock_config_obj.influxdb_bucket_weather = "weather_test"
        mock_config.return_value = mock_config_obj

        weather_data = {datetime.datetime(2025, 10, 18, 12, 0): {"Temperature": 15.5}}

        result = write_weather_to_influx(weather_data, dry_run=True)

        # Should succeed
        self.assertTrue(result)

        # InfluxClient should not be instantiated in dry-run
        mock_influx_class.assert_not_called()

    @patch("src.data_collection.weather.InfluxClient")
    @patch("src.data_collection.weather.get_config")
    def test_write_weather_to_influx_success(self, mock_config, mock_influx_class):
        """Test successful write to InfluxDB."""
        # Mock config
        mock_config_obj = Mock()
        mock_config_obj.influxdb_bucket_weather = "weather_test"
        mock_config.return_value = mock_config_obj

        # Mock InfluxClient
        mock_influx = Mock()
        mock_influx.write_weather.return_value = True
        mock_influx_class.return_value = mock_influx

        weather_data = {
            datetime.datetime(2025, 10, 18, 12, 0): {"Temperature": 15.5, "Wind speed": 3.2},
            datetime.datetime(2025, 10, 18, 12, 15): {"Temperature": 15.8, "Wind speed": 3.5},
        }

        result = write_weather_to_influx(weather_data, dry_run=False)

        # Should succeed
        self.assertTrue(result)

        # InfluxClient should be instantiated
        mock_influx_class.assert_called_once()

        # write_weather should be called with correct data
        mock_influx.write_weather.assert_called_once_with(weather_data)

    def test_write_weather_to_influx_empty(self):
        """Test handling of empty weather data."""
        result = write_weather_to_influx({}, dry_run=False)

        self.assertFalse(result)

    @patch("src.data_collection.weather.download_stored_query")
    def test_fetch_weather_forecast_no_data_in_response(self, mock_download):
        """Test handling when API returns None."""
        mock_download.return_value = None

        result = fetch_weather_forecast("60.1699,24.9384")

        self.assertEqual(result, {})

    @patch("src.data_collection.weather.download_stored_query")
    def test_fetch_weather_forecast_no_valid_times(self, mock_download):
        """Test handling when weather data has truthy data attribute but empty keys."""
        mock_weather = Mock()
        # Create a MagicMock with truthy value but empty keys
        mock_data = MagicMock()
        mock_data.__bool__ = lambda self: True
        mock_data.keys.return_value = []
        mock_weather.data = mock_data
        mock_download.return_value = mock_weather

        result = fetch_weather_forecast("60.1699,24.9384")

        # Should handle gracefully and return empty dict
        self.assertEqual(result, {})

    @patch("src.data_collection.weather.download_stored_query")
    def test_fetch_weather_forecast_unexpected_field_format(self, mock_download):
        """Test handling of unexpected field data format."""
        mock_weather = Mock()
        timestamp = datetime.datetime(2025, 10, 18, 12, 0)

        mock_weather.data = {
            timestamp: {
                "0": {
                    "Temperature": {"value": 15.5},
                    "BadField": "not_a_dict",  # Unexpected format
                }
            }
        }

        mock_download.return_value = mock_weather

        result = fetch_weather_forecast("60.1699,24.9384")

        # Should still process Temperature correctly
        self.assertEqual(len(result), 1)
        self.assertEqual(result[timestamp]["Temperature"], 15.5)
        self.assertNotIn("BadField", result[timestamp])

    @patch("json.dump")
    @patch("builtins.open", side_effect=OSError("Disk full"))
    @patch("os.makedirs")
    def test_save_weather_to_file_exception(self, mock_makedirs, mock_open, mock_json_dump):
        """Test exception handling in save_weather_to_file."""
        weather_data = {datetime.datetime(2025, 10, 18, 12, 0): {"Temperature": 15.5}}

        result = save_weather_to_file(weather_data, base_dir="/tmp/test")

        # Should return None on exception
        self.assertIsNone(result)

    @patch("src.data_collection.weather.InfluxClient")
    @patch("src.data_collection.weather.get_config")
    def test_write_weather_to_influx_failure(self, mock_config, mock_influx_class):
        """Test failed write to InfluxDB."""
        # Mock config
        mock_config_obj = Mock()
        mock_config_obj.influxdb_bucket_weather = "weather_test"
        mock_config.return_value = mock_config_obj

        # Mock InfluxClient to return failure
        mock_influx = Mock()
        mock_influx.write_weather.return_value = False
        mock_influx_class.return_value = mock_influx

        weather_data = {datetime.datetime(2025, 10, 18, 12, 0): {"Temperature": 15.5}}

        result = write_weather_to_influx(weather_data, dry_run=False)

        # Should return False
        self.assertFalse(result)

    @patch("src.data_collection.weather.InfluxClient")
    @patch("src.data_collection.weather.get_config")
    def test_write_weather_to_influx_exception(self, mock_config, mock_influx_class):
        """Test exception handling in write_weather_to_influx."""
        # Mock config
        mock_config_obj = Mock()
        mock_config.return_value = mock_config_obj

        # Mock InfluxClient to raise exception
        mock_influx_class.side_effect = Exception("Connection failed")

        weather_data = {datetime.datetime(2025, 10, 18, 12, 0): {"Temperature": 15.5}}

        result = write_weather_to_influx(weather_data, dry_run=False)

        # Should return False on exception
        self.assertFalse(result)

    @patch("src.data_collection.weather.InfluxClient")
    @patch("src.data_collection.weather.get_config")
    def test_write_weather_to_influx_dry_run_with_multiple_timestamps(
        self, mock_config, mock_influx_class
    ):
        """Test dry-run mode with multiple timestamps."""
        mock_config_obj = Mock()
        mock_config_obj.influxdb_bucket_weather = "weather_test"
        mock_config.return_value = mock_config_obj

        weather_data = {}
        for i in range(10):
            timestamp = datetime.datetime(2025, 10, 18, 12, 0) + datetime.timedelta(minutes=15 * i)
            weather_data[timestamp] = {
                "Temperature": 15.0 + i * 0.1,
                "Wind speed": 3.0 + i * 0.2,
                "Humidity": 80 - i,
            }

        result = write_weather_to_influx(weather_data, dry_run=True)

        # Should succeed
        self.assertTrue(result)
        # InfluxClient should not be instantiated
        mock_influx_class.assert_not_called()


class TestCollectWeather(unittest.TestCase):
    """Test the collect_weather function."""

    @patch("src.data_collection.weather.JSONDataLogger")
    @patch("src.data_collection.weather.fetch_weather_forecast")
    @patch("src.data_collection.weather.get_config")
    def test_collect_weather_success(self, mock_config, mock_fetch, mock_json_logger_class):
        """Test successful weather collection."""
        # Mock config
        mock_config_obj = Mock()
        mock_config_obj.get.return_value = "60.1699,24.9384"
        mock_config.return_value = mock_config_obj

        # Mock weather data
        weather_data = {
            datetime.datetime(2025, 10, 18, 12, 0): {"Temperature": 15.5, "Wind speed": 3.2}
        }
        mock_fetch.return_value = weather_data

        # Mock JSON logger
        mock_json_logger = Mock()
        mock_json_logger_class.return_value = mock_json_logger

        result = collect_weather()

        # Verify results
        self.assertEqual(result, weather_data)
        mock_fetch.assert_called_once_with("60.1699,24.9384")
        mock_json_logger.log_data.assert_called_once()
        mock_json_logger.cleanup_old_logs.assert_called_once()

    @patch("src.data_collection.weather.get_config")
    def test_collect_weather_no_latlon_config(self, mock_config):
        """Test collection when WEATHER_LATLON is not configured."""
        # Mock config with no weather_latlon
        mock_config_obj = Mock()
        mock_config_obj.get.return_value = None
        mock_config.return_value = mock_config_obj

        result = collect_weather()

        # Should return empty dict
        self.assertEqual(result, {})

    @patch("src.data_collection.weather.fetch_weather_forecast")
    @patch("src.data_collection.weather.get_config")
    def test_collect_weather_fetch_returns_empty(self, mock_config, mock_fetch):
        """Test collection when fetch returns no data."""
        # Mock config
        mock_config_obj = Mock()
        mock_config_obj.get.return_value = "60.1699,24.9384"
        mock_config.return_value = mock_config_obj

        # Mock fetch to return empty
        mock_fetch.return_value = {}

        result = collect_weather()

        # Should return empty dict
        self.assertEqual(result, {})


class TestMainFunction(unittest.TestCase):
    """Test the main entry point function."""

    @patch("src.data_collection.weather.write_weather_to_influx")
    @patch("src.data_collection.weather.collect_weather")
    @patch("sys.argv", ["weather.py"])
    def test_main_success(self, mock_collect, mock_write):
        """Test successful main execution."""
        weather_data = {datetime.datetime(2025, 10, 18, 12, 0): {"Temperature": 15.5}}
        mock_collect.return_value = weather_data
        mock_write.return_value = True

        result = main()

        self.assertEqual(result, 0)
        mock_collect.assert_called_once()
        mock_write.assert_called_once_with(weather_data, dry_run=False)

    @patch("src.data_collection.weather.write_weather_to_influx")
    @patch("src.data_collection.weather.collect_weather")
    @patch("sys.argv", ["weather.py", "--dry-run"])
    def test_main_dry_run(self, mock_collect, mock_write):
        """Test main with dry-run flag."""
        weather_data = {datetime.datetime(2025, 10, 18, 12, 0): {"Temperature": 15.5}}
        mock_collect.return_value = weather_data
        mock_write.return_value = True

        result = main()

        self.assertEqual(result, 0)
        mock_write.assert_called_once_with(weather_data, dry_run=True)

    @patch("src.data_collection.weather.collect_weather")
    @patch("sys.argv", ["weather.py"])
    def test_main_no_data_collected(self, mock_collect):
        """Test main when no data is collected."""
        mock_collect.return_value = {}

        result = main()

        self.assertEqual(result, 1)

    @patch("src.data_collection.weather.write_weather_to_influx")
    @patch("src.data_collection.weather.collect_weather")
    @patch("sys.argv", ["weather.py"])
    def test_main_write_failure(self, mock_collect, mock_write):
        """Test main when write to InfluxDB fails."""
        weather_data = {datetime.datetime(2025, 10, 18, 12, 0): {"Temperature": 15.5}}
        mock_collect.return_value = weather_data
        mock_write.return_value = False

        result = main()

        self.assertEqual(result, 1)

    @patch("src.data_collection.weather.collect_weather")
    @patch("sys.argv", ["weather.py"])
    def test_main_unhandled_exception(self, mock_collect):
        """Test main with unhandled exception."""
        mock_collect.side_effect = Exception("Unexpected error")

        result = main()

        self.assertEqual(result, 1)

    @patch("src.data_collection.weather.save_weather_to_file")
    @patch("src.data_collection.weather.write_weather_to_influx")
    @patch("src.data_collection.weather.collect_weather")
    @patch("sys.argv", ["weather.py", "--save-file"])
    def test_main_with_save_file(self, mock_collect, mock_write, mock_save):
        """Test main with --save-file flag."""
        weather_data = {datetime.datetime(2025, 10, 18, 12, 0): {"Temperature": 15.5}}
        mock_collect.return_value = weather_data
        mock_write.return_value = True
        mock_save.return_value = "/tmp/test/weather_data.json"

        result = main()

        self.assertEqual(result, 0)
        mock_save.assert_called_once_with(weather_data)

    @patch("src.data_collection.weather.write_weather_to_influx")
    @patch("src.data_collection.weather.collect_weather")
    @patch("sys.argv", ["weather.py", "--verbose"])
    def test_main_verbose_mode(self, mock_collect, mock_write):
        """Test main with verbose logging."""
        weather_data = {datetime.datetime(2025, 10, 18, 12, 0): {"Temperature": 15.5}}
        mock_collect.return_value = weather_data
        mock_write.return_value = True

        result = main()

        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
