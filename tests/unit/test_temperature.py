"""Unit tests for temperature data collection."""

import json
import time
import unittest
from unittest.mock import Mock, mock_open, patch

from src.data_collection.temperature import (
    SHELLY_HT_MAX_AGE_SECONDS,
    _prepare_influx_fields,
    collect_temperatures,
    convert_internal_id_to_influxid,
    get_temperature,
    get_temperature_meter_ids,
    load_shelly_ht_data,
    main,
    write_temperatures_to_influx,
)


class TestTemperatureCollection(unittest.TestCase):
    """Test temperature collection functions."""

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

    @patch("src.data_collection.temperature.get_config")
    def test_convert_internal_id_to_influxid_ds18b20(self, mock_config):
        """Test converting DS18B20 sensor IDs via direct match."""
        mock_config.return_value.sensor_mapping = {
            "28-0000000016a": "SensorA",
            "28-0000000023e": "SensorB",
        }
        self.assertEqual(convert_internal_id_to_influxid("28-0000000016a"), "SensorA")
        self.assertEqual(convert_internal_id_to_influxid("28-0000000023e"), "SensorB")

    @patch("src.data_collection.temperature.get_config")
    def test_convert_internal_id_to_influxid_suffix(self, mock_config):
        """Test suffix matching for DS18B20 sensors."""
        mock_config.return_value.sensor_mapping = {
            "28-0000000016a": "SensorA",
        }
        self.assertEqual(convert_internal_id_to_influxid("28-other6a"), "SensorA")

    @patch("src.data_collection.temperature.get_config")
    def test_convert_internal_id_to_influxid_shelly(self, mock_config):
        """Test converting Shelly sensor IDs."""
        mock_config.return_value.sensor_mapping = {
            "shellyht-AABB-180": "GarageSensor",
        }
        self.assertEqual(convert_internal_id_to_influxid("shellyht-AABB-180"), "GarageSensor")

    @patch("src.data_collection.temperature.get_config")
    def test_convert_internal_id_to_influxid_unknown(self, mock_config):
        """Test handling of unknown sensor IDs."""
        mock_config.return_value.sensor_mapping = {}
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

    @patch("src.data_collection.temperature.get_config")
    def test_convert_internal_id_to_influxid_shelly_variant(self, mock_config):
        """Test converting Shelly sensor IDs with different patterns."""
        mock_config.return_value.sensor_mapping = {"device-191": "BathroomSensor"}
        self.assertEqual(convert_internal_id_to_influxid("shelly-191"), "BathroomSensor")

    @patch("src.data_collection.temperature.get_config")
    def test_convert_internal_id_to_influxid_hyphen19_pattern(self, mock_config):
        """Test converting sensor IDs with -19X pattern."""
        mock_config.return_value.sensor_mapping = {"SomeDevice-190": "BedroomSensor"}
        self.assertEqual(convert_internal_id_to_influxid("device-190"), "BedroomSensor")

    @patch("src.data_collection.temperature.get_config")
    def test_convert_internal_id_to_influxid_not_in_mapping(self, mock_config):
        """Test converting valid format but sensor not in mapping."""
        mock_config.return_value.sensor_mapping = {}
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
        mock_config_obj.sensor_mapping = {
            "28-000006a": "SensorA",
            "28-00003e": "SensorB",
        }
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
        self.assertEqual(call_args[1]["fields"]["SensorA"], 21.5)
        self.assertEqual(call_args[1]["fields"]["SensorB"], 22.0)

    @patch("src.data_collection.temperature.get_config")
    def test_write_temperatures_to_influx_dry_run(self, mock_config):
        """Test dry-run mode (no actual write)."""
        mock_config_obj = Mock()
        mock_config_obj.influxdb_bucket_temperatures = "temperatures_test"
        mock_config_obj.sensor_mapping = {"28-000006a": "SensorA"}
        mock_config.return_value = mock_config_obj

        temp_status = {"28-000006a": {"temp": 21.5, "updated": 1234567890.0}}

        result = write_temperatures_to_influx(temp_status, dry_run=True)

        self.assertTrue(result)

    @patch("src.data_collection.temperature.get_config")
    def test_write_temperatures_to_influx_no_valid_fields(self, mock_config):
        """Test handling of no valid temperature fields."""
        mock_config_obj = Mock()
        mock_config_obj.sensor_mapping = {}
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
        mock_config_obj.sensor_mapping = {"28-000006a": "SensorA"}
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
        mock_config_obj.sensor_mapping = {"28-000006a": "SensorA"}
        mock_config.return_value = mock_config_obj

        # Make InfluxClient throw exception
        mock_influx_cls.side_effect = Exception("InfluxDB connection error")

        temp_status = {"28-000006a": {"temp": 21.5, "updated": 1234567890.0}}

        result = write_temperatures_to_influx(temp_status)

        self.assertFalse(result)


class TestLoadShellyHtData(unittest.TestCase):
    """Test Shelly HT data loading from temperature_status.json."""

    def test_load_shelly_ht_data_success(self):
        """Test loading valid Shelly HT data."""
        now = time.time()
        status_data = {
            "shellyht-02D824-180": {"temp": 6.12, "hum": 72.0, "updated": now - 60},
            "MasterBedroom-190": {"temp": 19.3, "hum": 35.0, "updated": now - 120},
            "28-00000de4d34a": {"temp": 20.75, "updated": now - 10},
        }
        json_str = json.dumps(status_data)

        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=json_str)):
                result = load_shelly_ht_data("/fake/path.json")

        self.assertEqual(len(result), 2)
        self.assertIn("shellyht-02D824-180", result)
        self.assertIn("MasterBedroom-190", result)
        self.assertNotIn("28-00000de4d34a", result)
        self.assertEqual(result["shellyht-02D824-180"]["hum"], 72.0)

    def test_load_shelly_ht_data_skips_stale(self):
        """Test that stale Shelly HT data is skipped."""
        old_time = time.time() - SHELLY_HT_MAX_AGE_SECONDS - 100
        status_data = {
            "shellyht-02D824-180": {"temp": 6.12, "hum": 72.0, "updated": old_time},
        }
        json_str = json.dumps(status_data)

        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=json_str)):
                result = load_shelly_ht_data("/fake/path.json")

        self.assertEqual(len(result), 0)

    def test_load_shelly_ht_data_file_missing(self):
        """Test graceful handling when status file doesn't exist."""
        with patch("os.path.exists", return_value=False):
            result = load_shelly_ht_data("/fake/path.json")

        self.assertEqual(result, {})

    def test_load_shelly_ht_data_invalid_json(self):
        """Test graceful handling of corrupt JSON file."""
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data="not valid json{")):
                result = load_shelly_ht_data("/fake/path.json")

        self.assertEqual(result, {})

    def test_load_shelly_ht_data_skips_no_temp(self):
        """Test that entries without temp are skipped."""
        now = time.time()
        status_data = {
            "shellyht-02D824-180": {"hum": 72.0, "updated": now - 60},
        }
        json_str = json.dumps(status_data)

        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=json_str)):
                result = load_shelly_ht_data("/fake/path.json")

        self.assertEqual(len(result), 0)


class TestPrepareInfluxFields(unittest.TestCase):
    """Test _prepare_influx_fields helper."""

    @patch("src.data_collection.temperature.get_config")
    def test_temps_and_humidity(self, mock_config):
        """Test extracting both temperature and humidity fields."""
        mock_config.return_value.sensor_mapping = {
            "28-000006a": "SensorA",
            "shellyht-AABB-180": "GarageSensor",
        }
        status = {
            "28-000006a": {"temp": 21.5, "updated": 1234567890.0},
            "shellyht-AABB-180": {"temp": 6.12, "hum": 72.0, "updated": 1234567890.0},
        }
        temp_fields, hum_fields = _prepare_influx_fields(status)

        self.assertEqual(temp_fields["SensorA"], 21.5)
        self.assertEqual(temp_fields["GarageSensor"], 6.12)
        self.assertEqual(hum_fields["GarageSensor"], 72.0)
        self.assertNotIn("SensorA", hum_fields)

    @patch("src.data_collection.temperature.get_config")
    def test_no_humidity(self, mock_config):
        """Test that 1-wire sensors produce no humidity fields."""
        mock_config.return_value.sensor_mapping = {
            "28-000006a": "SensorA",
            "28-00003e": "SensorB",
        }
        status = {
            "28-000006a": {"temp": 21.5, "updated": 1234567890.0},
            "28-00003e": {"temp": 22.0, "updated": 1234567890.0},
        }
        temp_fields, hum_fields = _prepare_influx_fields(status)

        self.assertEqual(len(temp_fields), 2)
        self.assertEqual(len(hum_fields), 0)

    @patch("src.data_collection.temperature.get_config")
    def test_unknown_sensor_skipped(self, mock_config):
        """Test that unknown sensor IDs are skipped."""
        mock_config.return_value.sensor_mapping = {}
        status = {
            "unknown-xyz": {"temp": 21.5, "hum": 50.0, "updated": 1234567890.0},
        }
        temp_fields, hum_fields = _prepare_influx_fields(status)

        self.assertEqual(len(temp_fields), 0)
        self.assertEqual(len(hum_fields), 0)


class TestWriteHumidityToInflux(unittest.TestCase):
    """Test humidity writing in write_temperatures_to_influx."""

    @patch("src.data_collection.temperature.get_config")
    @patch("src.data_collection.temperature.InfluxClient")
    def test_writes_both_temps_and_humidity(self, mock_influx_cls, mock_config):
        """Test that both temperatures and humidities are written."""
        mock_config_obj = Mock()
        mock_config_obj.influxdb_bucket_temperatures = "temperatures_test"
        mock_config_obj.sensor_mapping = {
            "28-000006a": "SensorA",
            "shellyht-AABB-180": "GarageSensor",
        }
        mock_config.return_value = mock_config_obj

        mock_influx = Mock()
        mock_influx.write_point.return_value = True
        mock_influx_cls.return_value = mock_influx

        temp_status = {
            "28-000006a": {"temp": 21.5, "updated": 1234567890.0},
            "shellyht-AABB-180": {"temp": 6.12, "hum": 72.0, "updated": 1234567890.0},
        }

        result = write_temperatures_to_influx(temp_status)

        self.assertTrue(result)
        self.assertEqual(mock_influx.write_point.call_count, 2)
        calls = mock_influx.write_point.call_args_list
        self.assertEqual(calls[0][1]["measurement"], "temperatures")
        self.assertEqual(calls[1][1]["measurement"], "humidities")
        self.assertEqual(calls[1][1]["fields"]["GarageSensor"], 72.0)

    @patch("src.data_collection.temperature.get_config")
    @patch("src.data_collection.temperature.InfluxClient")
    def test_skips_humidity_when_none(self, mock_influx_cls, mock_config):
        """Test that humidity write is skipped when no humidity data."""
        mock_config_obj = Mock()
        mock_config_obj.sensor_mapping = {"28-000006a": "SensorA"}
        mock_config.return_value = mock_config_obj

        mock_influx = Mock()
        mock_influx.write_point.return_value = True
        mock_influx_cls.return_value = mock_influx

        temp_status = {
            "28-000006a": {"temp": 21.5, "updated": 1234567890.0},
        }

        result = write_temperatures_to_influx(temp_status)

        self.assertTrue(result)
        mock_influx.write_point.assert_called_once()

    @patch("src.data_collection.temperature.get_config")
    def test_dry_run_shows_humidity(self, mock_config):
        """Test that dry-run logs humidity data."""
        mock_config_obj = Mock()
        mock_config_obj.influxdb_bucket_temperatures = "temperatures_test"
        mock_config_obj.sensor_mapping = {
            "28-000006a": "SensorA",
            "shellyht-AABB-180": "GarageSensor",
        }
        mock_config.return_value = mock_config_obj

        temp_status = {
            "28-000006a": {"temp": 21.5, "updated": 1234567890.0},
            "shellyht-AABB-180": {"temp": 6.12, "hum": 72.0, "updated": 1234567890.0},
        }

        result = write_temperatures_to_influx(temp_status, dry_run=True)

        self.assertTrue(result)


class TestCollectTemperaturesWithShelly(unittest.TestCase):
    """Test collect_temperatures merges Shelly HT data."""

    @patch("src.data_collection.temperature.load_shelly_ht_data")
    @patch("src.data_collection.temperature.get_temperature_meter_ids")
    @patch("src.data_collection.temperature.get_temperature")
    def test_merges_shelly_data(self, mock_get_temp, mock_get_ids, mock_shelly):
        """Test that Shelly HT data is merged with 1-wire data."""
        mock_get_ids.return_value = ["28-000006a"]
        mock_get_temp.return_value = 21.5
        mock_shelly.return_value = {
            "shellyht-02D824-180": {"temp": 6.12, "hum": 72.0, "updated": 1234567890.0},
        }

        result = collect_temperatures()

        self.assertEqual(len(result), 2)
        self.assertIn("28-000006a", result)
        self.assertIn("shellyht-02D824-180", result)
        self.assertEqual(result["shellyht-02D824-180"]["hum"], 72.0)


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
