"""Unit tests for weather data collection."""

import unittest
from unittest.mock import Mock, patch, MagicMock
import datetime

from src.data_collection.weather import (
    fetch_weather_forecast,
    save_weather_to_file,
    write_weather_to_influx,
    EXCLUDED_FIELDS
)


class TestWeatherCollection(unittest.TestCase):
    """Test weather collection functions."""

    def test_excluded_fields_no_unicode(self):
        """Verify excluded fields contain no unicode characters."""
        for field in EXCLUDED_FIELDS:
            self.assertTrue(field.isascii(), f"Field {field} contains non-ASCII characters")

    @patch('src.data_collection.weather.download_stored_query')
    def test_fetch_weather_forecast_success(self, mock_download):
        """Test successful weather forecast fetch."""
        # Mock weather data
        mock_weather = Mock()
        timestamp1 = datetime.datetime(2025, 10, 18, 12, 0)
        timestamp2 = datetime.datetime(2025, 10, 18, 12, 15)

        mock_weather.data = {
            timestamp1: {
                '0': {
                    'Temperature': {'value': 15.5},
                    'Wind speed': {'value': 3.2},
                    'Geopotential height': {'value': 100}  # Should be excluded
                }
            },
            timestamp2: {
                '0': {
                    'Temperature': {'value': 15.8},
                    'Wind speed': {'value': 3.5}
                }
            }
        }

        mock_download.return_value = mock_weather

        result = fetch_weather_forecast("60.1699,24.9384")

        # Verify results
        self.assertEqual(len(result), 2)
        self.assertIn(timestamp1, result)
        self.assertIn(timestamp2, result)

        # Check first timestamp
        self.assertEqual(result[timestamp1]['Temperature'], 15.5)
        self.assertEqual(result[timestamp1]['Wind speed'], 3.2)
        self.assertNotIn('Geopotential height', result[timestamp1])  # Excluded

        # Check second timestamp
        self.assertEqual(result[timestamp2]['Temperature'], 15.8)

    @patch('src.data_collection.weather.download_stored_query')
    def test_fetch_weather_forecast_empty(self, mock_download):
        """Test handling of empty weather data."""
        mock_weather = Mock()
        mock_weather.data = {}
        mock_download.return_value = mock_weather

        result = fetch_weather_forecast("60.1699,24.9384")

        self.assertEqual(result, {})

    @patch('src.data_collection.weather.download_stored_query')
    def test_fetch_weather_forecast_exception(self, mock_download):
        """Test handling of API exceptions."""
        mock_download.side_effect = Exception("API error")

        result = fetch_weather_forecast("60.1699,24.9384")

        self.assertEqual(result, {})

    @patch('builtins.open', create=True)
    @patch('os.makedirs')
    @patch('json.dump')
    def test_save_weather_to_file(self, mock_json_dump, mock_makedirs, mock_open):
        """Test saving weather data to file."""
        weather_data = {
            datetime.datetime(2025, 10, 18, 12, 0): {
                'Temperature': 15.5,
                'Wind speed': 3.2
            }
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

    @patch('src.data_collection.weather.InfluxClient')
    def test_write_weather_to_influx_dry_run(self, mock_influx_class):
        """Test dry-run mode doesn't write to InfluxDB."""
        weather_data = {
            datetime.datetime(2025, 10, 18, 12, 0): {
                'Temperature': 15.5
            }
        }

        result = write_weather_to_influx(weather_data, dry_run=True)

        # Should succeed
        self.assertTrue(result)

        # InfluxClient should not be instantiated in dry-run
        mock_influx_class.assert_not_called()

    @patch('src.data_collection.weather.InfluxClient')
    @patch('src.data_collection.weather.get_config')
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
            datetime.datetime(2025, 10, 18, 12, 0): {
                'Temperature': 15.5,
                'Wind speed': 3.2
            },
            datetime.datetime(2025, 10, 18, 12, 15): {
                'Temperature': 15.8,
                'Wind speed': 3.5
            }
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


if __name__ == '__main__':
    unittest.main()
