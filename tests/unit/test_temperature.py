"""Unit tests for temperature data collection."""

import unittest
from unittest.mock import Mock, mock_open, patch

from src.data_collection.temperature import (
    SENSOR_NAMES,
    collect_temperatures,
    convert_internal_id_to_influxid,
    get_temperature,
    get_temperature_meter_ids,
    main,
    write_temperatures_to_influx,
)


class TestTemperatureCollection(unittest.TestCase):
    """Test temperature collection functions."""

    def test_sensor_names_no_unicode(self):
        """Verify sensor names contain no unicode characters."""
        for name in SENSOR_NAMES.values():
            self.assertTrue(name.isascii(), f"Name {name} contains non-ASCII characters")

    @patch("os.popen")
    def test_get_temperature_meter_ids(self, mock_popen):
        """Test getting temperature meter IDs."""
        mock_result = Mock()
        mock_result.read.return_value = "28-000006a\n28-00003e\nw1_bus_master1\n"
        mock_popen.return_value = mock_result

        result = get_temperature_meter_ids()

        self.assertEqual(result, ["28-000006a", "28-00003e", "w1_bus_master1"])
        mock_popen.assert_called_once_with("ls /sys/bus/w1/devices 2> /dev/null")

    @patch("os.popen")
    def test_get_temperature_meter_ids_error(self, mock_popen):
        """Test handling of errors when getting meter IDs."""
        mock_popen.side_effect = Exception("Command failed")

        result = get_temperature_meter_ids()

        self.assertEqual(result, [])

    def test_convert_internal_id_to_influxid_ds18b20(self):
        """Test converting DS18B20 sensor IDs."""
        self.assertEqual(convert_internal_id_to_influxid("28-000006a"), "Savupiippu")
        self.assertEqual(convert_internal_id_to_influxid("28-00003e"), "Valto")

    def test_convert_internal_id_to_influxid_shelly(self):
        """Test converting Shelly sensor IDs."""
        self.assertEqual(convert_internal_id_to_influxid("shelly-180"), "Autotalli")

    def test_convert_internal_id_to_influxid_unknown(self):
        """Test handling of unknown sensor IDs."""
        result = convert_internal_id_to_influxid("unknown-type-123")
        self.assertIsNone(result)

    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("time.sleep")
    def test_get_temperature_valid_reading(self, mock_sleep, mock_file, mock_isfile):
        """Test reading valid temperature."""
        mock_isfile.return_value = True

        # Simulate consistent sensor readings
        mock_file.return_value.read.return_value = (
            "50 01 4b 46 7f ff 0c 10 1c : crc=1c YES\n" "50 01 4b 46 7f ff 0c 10 1c t=21000"
        )

        result = get_temperature("28-000006a")

        self.assertIsNotNone(result)
        self.assertEqual(result, 21.0)

    @patch("os.path.isfile")
    def test_get_temperature_missing_device(self, mock_isfile):
        """Test handling of missing sensor device."""
        mock_isfile.return_value = False

        result = get_temperature("28-000006a")

        self.assertIsNone(result)

    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("time.sleep")
    def test_get_temperature_suspicious_values(self, mock_sleep, mock_file, mock_isfile):
        """Test filtering of suspicious temperature values."""
        mock_isfile.return_value = True

        # Simulate reading of 85C (common error value)
        mock_file.return_value.read.return_value = (
            "50 01 4b 46 7f ff 0c 10 1c : crc=1c YES\n" "50 01 4b 46 7f ff 0c 10 1c t=85000"
        )

        result = get_temperature("28-000006a")

        # Should reject 85C on first reading
        self.assertIsNone(result)

    @patch("src.data_collection.temperature.get_temperature_meter_ids")
    @patch("src.data_collection.temperature.get_temperature")
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

    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("time.sleep")
    def test_get_temperature_exceeds_threshold(self, mock_sleep, mock_file, mock_isfile):
        """Test handling of temperature exceeding upper threshold."""
        mock_isfile.return_value = True

        # First reading above threshold, second reading valid
        read_count = [0]

        def read_side_effect():
            read_count[0] += 1
            if read_count[0] <= 3:
                # First few reads: temperature too high (>100C)
                return (
                    "50 01 4b 46 7f ff 0c 10 1c : crc=1c YES\n50 01 4b 46 7f ff 0c 10 1c t=150000"
                )
            else:
                # Later reads: valid temperature
                return "50 01 4b 46 7f ff 0c 10 1c : crc=1c YES\n50 01 4b 46 7f ff 0c 10 1c t=21000"

        mock_file.return_value.read.side_effect = read_side_effect

        result = get_temperature("28-000006a")

        self.assertIsNotNone(result)
        self.assertEqual(result, 21.0)

    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("time.sleep")
    def test_get_temperature_median_fallback(self, mock_sleep, mock_file, mock_isfile):
        """Test fallback to median when no 3 identical readings."""
        mock_isfile.return_value = True

        # Simulate varying readings that require median fallback
        readings = [
            "50 01 4b 46 7f ff 0c 10 1c : crc=1c YES\n50 01 4b 46 7f ff 0c 10 1c t=21000",
            "50 01 4b 46 7f ff 0c 10 1c : crc=1c YES\n50 01 4b 46 7f ff 0c 10 1c t=21000",
            "50 01 4b 46 7f ff 0c 10 1c : crc=1c YES\n50 01 4b 46 7f ff 0c 10 1c t=22000",
            "50 01 4b 46 7f ff 0c 10 1c : crc=1c YES\n50 01 4b 46 7f ff 0c 10 1c t=22000",
        ]
        mock_file.return_value.read.side_effect = readings * 5

        result = get_temperature("28-000006a")

        # Should use median fallback
        self.assertIsNotNone(result)
        self.assertIn(result, [21.0, 22.0])

    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("time.sleep")
    def test_get_temperature_out_of_range(self, mock_sleep, mock_file, mock_isfile):
        """Test rejection of temperature outside valid DS18B20 range."""
        mock_isfile.return_value = True

        # Temperature below -55C (DS18B20 minimum)
        mock_file.return_value.read.return_value = (
            "50 01 4b 46 7f ff 0c 10 1c : crc=1c YES\n" "50 01 4b 46 7f ff 0c 10 1c t=-60000"
        )

        result = get_temperature("28-000006a")

        self.assertIsNone(result)

    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("time.sleep")
    def test_get_temperature_zero_value_suspicious(self, mock_sleep, mock_file, mock_isfile):
        """Test filtering of 0C as suspicious value on first read."""
        mock_isfile.return_value = True

        # Reading of 0C (common error)
        mock_file.return_value.read.return_value = (
            "50 01 4b 46 7f ff 0c 10 1c : crc=1c YES\n" "50 01 4b 46 7f ff 0c 10 1c t=0"
        )

        result = get_temperature("28-000006a")

        # Should reject 0C on first reading
        self.assertIsNone(result)

    @patch("os.path.isfile")
    @patch("builtins.open")
    @patch("time.sleep")
    def test_get_temperature_read_exception(self, mock_sleep, mock_file, mock_isfile):
        """Test handling of file read exception."""
        mock_isfile.return_value = True
        mock_file.side_effect = OSError("Read error")

        result = get_temperature("28-000006a")

        self.assertIsNone(result)

    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("time.sleep")
    def test_get_temperature_max_tries_exceeded(self, mock_sleep, mock_file, mock_isfile):
        """Test when max tries exceeded without valid reading."""
        mock_isfile.return_value = True

        # Always return CRC failure (no YES)
        mock_file.return_value.read.return_value = (
            "50 01 4b 46 7f ff 0c 10 1c : crc=1c NO\n" "50 01 4b 46 7f ff 0c 10 1c t=21000"
        )

        result = get_temperature("28-000006a")

        self.assertIsNone(result)

    def test_convert_internal_id_to_influxid_shelly_variant(self):
        """Test converting Shelly sensor IDs with different patterns."""
        self.assertEqual(convert_internal_id_to_influxid("shelly-191"), "YlakertaKH")

    def test_convert_internal_id_to_influxid_hyphen19_pattern(self):
        """Test converting sensor IDs with -19X pattern."""
        self.assertEqual(convert_internal_id_to_influxid("device-190"), "PaaMH3")

    def test_convert_internal_id_to_influxid_not_in_mapping(self):
        """Test converting valid format but sensor not in mapping."""
        result = convert_internal_id_to_influxid("28-000099")
        self.assertIsNone(result)


class TestWriteToInflux(unittest.TestCase):
    """Test writing temperature data to InfluxDB."""

    @patch("src.data_collection.temperature.get_config")
    @patch("src.data_collection.temperature.InfluxClient")
    def test_write_temperatures_to_influx_success(self, mock_influx_cls, mock_config):
        """Test successful write to InfluxDB."""
        mock_config_obj = Mock()
        mock_config_obj.influxdb_bucket_temperatures = "temperatures_test"
        mock_config.return_value = mock_config_obj

        mock_influx = Mock()
        mock_influx.write_point.return_value = True
        mock_influx_cls.return_value = mock_influx

        temp_status = {
            "28-000006a": {"temp": 21.5, "updated": 1234567890.0},
            "28-00003e": {"temp": 22.0, "updated": 1234567890.0},
        }

        result = write_temperatures_to_influx(temp_status)

        self.assertTrue(result)
        mock_influx.write_point.assert_called_once()
        call_args = mock_influx.write_point.call_args
        self.assertEqual(call_args[1]["measurement"], "temperatures")
        self.assertEqual(call_args[1]["fields"]["Savupiippu"], 21.5)
        self.assertEqual(call_args[1]["fields"]["Valto"], 22.0)

    @patch("src.data_collection.temperature.get_config")
    def test_write_temperatures_to_influx_dry_run(self, mock_config):
        """Test dry-run mode (no actual write)."""
        mock_config_obj = Mock()
        mock_config_obj.influxdb_bucket_temperatures = "temperatures_test"
        mock_config.return_value = mock_config_obj

        temp_status = {"28-000006a": {"temp": 21.5, "updated": 1234567890.0}}

        result = write_temperatures_to_influx(temp_status, dry_run=True)

        self.assertTrue(result)

    @patch("src.data_collection.temperature.get_config")
    def test_write_temperatures_to_influx_no_valid_fields(self, mock_config):
        """Test handling of no valid temperature fields."""
        mock_config_obj = Mock()
        mock_config.return_value = mock_config_obj

        # Unknown sensor ID that won't convert
        temp_status = {"unknown-sensor": {"temp": 21.5, "updated": 1234567890.0}}

        result = write_temperatures_to_influx(temp_status)

        self.assertFalse(result)

    @patch("src.data_collection.temperature.get_config")
    @patch("src.data_collection.temperature.InfluxClient")
    def test_write_temperatures_to_influx_write_failure(self, mock_influx_cls, mock_config):
        """Test handling of InfluxDB write failure."""
        mock_config_obj = Mock()
        mock_config.return_value = mock_config_obj

        mock_influx = Mock()
        mock_influx.write_point.return_value = False
        mock_influx_cls.return_value = mock_influx

        temp_status = {"28-000006a": {"temp": 21.5, "updated": 1234567890.0}}

        result = write_temperatures_to_influx(temp_status)

        self.assertFalse(result)

    @patch("src.data_collection.temperature.get_config")
    @patch("src.data_collection.temperature.InfluxClient")
    def test_write_temperatures_to_influx_exception(self, mock_influx_cls, mock_config):
        """Test handling of exception during write."""
        mock_config_obj = Mock()
        mock_config.return_value = mock_config_obj

        # Make InfluxClient throw exception
        mock_influx_cls.side_effect = Exception("InfluxDB connection error")

        temp_status = {"28-000006a": {"temp": 21.5, "updated": 1234567890.0}}

        result = write_temperatures_to_influx(temp_status)

        self.assertFalse(result)


class TestMain(unittest.TestCase):
    """Test main entry point."""

    @patch.dict("os.environ", {"STAGING_MODE": "true"})
    @patch("sys.argv", ["temperature.py"])
    def test_main_staging_mode(self):
        """Test that staging mode prevents temperature collection."""
        result = main()
        self.assertEqual(result, 0)

    @patch.dict("os.environ", {}, clear=True)
    @patch("sys.argv", ["temperature.py", "--dry-run"])
    @patch("src.data_collection.temperature.collect_temperatures")
    @patch("src.data_collection.temperature.write_temperatures_to_influx")
    def test_main_dry_run_success(self, mock_write, mock_collect):
        """Test successful dry-run execution."""
        mock_collect.return_value = {"28-000006a": {"temp": 21.5, "updated": 1234567890.0}}
        mock_write.return_value = True

        result = main()

        self.assertEqual(result, 0)
        mock_write.assert_called_once()
        # Check dry_run keyword argument
        self.assertTrue(mock_write.call_args.kwargs.get("dry_run", False))

    @patch.dict("os.environ", {}, clear=True)
    @patch("sys.argv", ["temperature.py"])
    @patch("src.data_collection.temperature.collect_temperatures")
    @patch("src.data_collection.temperature.write_temperatures_to_influx")
    def test_main_normal_success(self, mock_write, mock_collect):
        """Test successful normal execution."""
        mock_collect.return_value = {"28-000006a": {"temp": 21.5, "updated": 1234567890.0}}
        mock_write.return_value = True

        result = main()

        self.assertEqual(result, 0)
        mock_write.assert_called_once()
        # Check dry_run keyword argument is False
        self.assertFalse(mock_write.call_args.kwargs.get("dry_run", False))

    @patch.dict("os.environ", {}, clear=True)
    @patch("sys.argv", ["temperature.py"])
    @patch("src.data_collection.temperature.collect_temperatures")
    def test_main_no_temperatures(self, mock_collect):
        """Test handling of no temperatures collected."""
        mock_collect.return_value = {}

        result = main()

        self.assertEqual(result, 1)

    @patch.dict("os.environ", {}, clear=True)
    @patch("sys.argv", ["temperature.py"])
    @patch("src.data_collection.temperature.collect_temperatures")
    @patch("src.data_collection.temperature.write_temperatures_to_influx")
    def test_main_write_failure(self, mock_write, mock_collect):
        """Test handling of write failure."""
        mock_collect.return_value = {"28-000006a": {"temp": 21.5, "updated": 1234567890.0}}
        mock_write.return_value = False

        result = main()

        self.assertEqual(result, 1)

    @patch.dict("os.environ", {}, clear=True)
    @patch("sys.argv", ["temperature.py"])
    @patch("src.data_collection.temperature.collect_temperatures")
    def test_main_exception(self, mock_collect):
        """Test handling of unhandled exception."""
        mock_collect.side_effect = Exception("Unexpected error")

        result = main()

        self.assertEqual(result, 1)

    @patch.dict("os.environ", {}, clear=True)
    @patch("sys.argv", ["temperature.py", "--verbose"])
    @patch("src.data_collection.temperature.collect_temperatures")
    @patch("src.data_collection.temperature.write_temperatures_to_influx")
    def test_main_verbose_mode(self, mock_write, mock_collect):
        """Test verbose mode sets logging level."""
        mock_collect.return_value = {"28-000006a": {"temp": 21.5, "updated": 1234567890.0}}
        mock_write.return_value = True

        result = main()

        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
