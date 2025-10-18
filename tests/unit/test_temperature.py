"""Unit tests for temperature data collection."""

import unittest
from unittest.mock import Mock, patch, mock_open
import datetime

from src.data_collection.temperature import (
    get_temperature_meter_ids,
    get_temperature,
    convert_internal_id_to_influxid,
    collect_temperatures,
    SENSOR_NAMES
)


class TestTemperatureCollection(unittest.TestCase):
    """Test temperature collection functions."""

    def test_sensor_names_no_unicode(self):
        """Verify sensor names contain no unicode characters."""
        for name in SENSOR_NAMES.values():
            self.assertTrue(name.isascii(), f"Name {name} contains non-ASCII characters")

    @patch('os.popen')
    def test_get_temperature_meter_ids(self, mock_popen):
        """Test getting temperature meter IDs."""
        mock_result = Mock()
        mock_result.read.return_value = "28-000006a\n28-00003e\nw1_bus_master1\n"
        mock_popen.return_value = mock_result

        result = get_temperature_meter_ids()

        self.assertEqual(result, ["28-000006a", "28-00003e", "w1_bus_master1"])
        mock_popen.assert_called_once_with('ls /sys/bus/w1/devices 2> /dev/null')

    @patch('os.popen')
    def test_get_temperature_meter_ids_error(self, mock_popen):
        """Test handling of errors when getting meter IDs."""
        mock_popen.side_effect = Exception("Command failed")

        result = get_temperature_meter_ids()

        self.assertEqual(result, [])

    def test_convert_internal_id_to_influxid_ds18b20(self):
        """Test converting DS18B20 sensor IDs."""
        self.assertEqual(
            convert_internal_id_to_influxid("28-000006a"),
            "Savupiippu"
        )
        self.assertEqual(
            convert_internal_id_to_influxid("28-00003e"),
            "Valto"
        )

    def test_convert_internal_id_to_influxid_shelly(self):
        """Test converting Shelly sensor IDs."""
        self.assertEqual(
            convert_internal_id_to_influxid("shelly-180"),
            "Autotalli"
        )

    def test_convert_internal_id_to_influxid_unknown(self):
        """Test handling of unknown sensor IDs."""
        result = convert_internal_id_to_influxid("unknown-type-123")
        self.assertIsNone(result)

    @patch('os.path.isfile')
    @patch('builtins.open', new_callable=mock_open)
    @patch('time.sleep')
    def test_get_temperature_valid_reading(self, mock_sleep, mock_file, mock_isfile):
        """Test reading valid temperature."""
        mock_isfile.return_value = True

        # Simulate consistent sensor readings
        mock_file.return_value.read.return_value = (
            "50 01 4b 46 7f ff 0c 10 1c : crc=1c YES\n"
            "50 01 4b 46 7f ff 0c 10 1c t=21000"
        )

        result = get_temperature("28-000006a")

        self.assertIsNotNone(result)
        self.assertEqual(result, 21.0)

    @patch('os.path.isfile')
    def test_get_temperature_missing_device(self, mock_isfile):
        """Test handling of missing sensor device."""
        mock_isfile.return_value = False

        result = get_temperature("28-000006a")

        self.assertIsNone(result)

    @patch('os.path.isfile')
    @patch('builtins.open', new_callable=mock_open)
    @patch('time.sleep')
    def test_get_temperature_suspicious_values(self, mock_sleep, mock_file, mock_isfile):
        """Test filtering of suspicious temperature values."""
        mock_isfile.return_value = True

        # Simulate reading of 85C (common error value)
        mock_file.return_value.read.return_value = (
            "50 01 4b 46 7f ff 0c 10 1c : crc=1c YES\n"
            "50 01 4b 46 7f ff 0c 10 1c t=85000"
        )

        result = get_temperature("28-000006a")

        # Should reject 85C on first reading
        self.assertIsNone(result)

    @patch('src.data_collection.temperature.get_temperature_meter_ids')
    @patch('src.data_collection.temperature.get_temperature')
    def test_collect_temperatures(self, mock_get_temp, mock_get_ids):
        """Test collecting temperatures from multiple sensors."""
        mock_get_ids.return_value = ["28-000006a", "28-00003e", "28-000e9"]
        mock_get_temp.side_effect = [21.5, 22.0, None]  # Third sensor fails

        result = collect_temperatures()

        # Should have 2 successful readings (e9 skipped, third returned None)
        self.assertEqual(len(result), 2)
        self.assertIn("28-000006a", result)
        self.assertIn("28-00003e", result)
        self.assertNotIn("28-000e9", result)  # Should be skipped
        self.assertEqual(result["28-000006a"]["temp"], 21.5)


if __name__ == '__main__':
    unittest.main()
