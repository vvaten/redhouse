"""Unit tests for configuration management."""

import os
import tempfile
import unittest
from unittest.mock import patch

from src.common.config import Config, get_config

# Minimal valid config.yaml content for tests
MINIMAL_CONFIG_YAML = """
heating:
  curve:
    -20: 10
    0: 6
    16: 2
  evuoff_threshold_price: 0.30
  evuoff_max_continuous_hours: 3
hardware:
  pump_i2c_bus: 1
  pump_i2c_address: 0x10
  shelly_relay_url: http://192.168.1.5
  shelly_em3_url: http://192.168.1.5
data_collection:
  spot_prices:
    value_added_tax: 1.255
    sellers_margin: 0.50
    production_buyback_margin: 0.30
    transfer_day_price: 2.59
    transfer_night_price: 1.35
    transfer_tax_price: 2.79372
logging:
  level: INFO
  dir: /var/log/redhouse
  max_bytes: 10485760
  backup_count: 5
"""


def _make_temp_config(yaml_content=MINIMAL_CONFIG_YAML):
    """Create a temporary config.yaml file and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    f.write(yaml_content)
    f.close()
    return f.name


class TestConfig(unittest.TestCase):
    """Test configuration loading and management."""

    def setUp(self):
        """Set up test fixtures."""
        import src.common.config

        src.common.config._config = None

    def test_config_requires_yaml_file(self):
        """Test that Config raises FileNotFoundError when config.yaml is missing."""
        with self.assertRaises(FileNotFoundError) as ctx:
            Config(config_path="/nonexistent/config.yaml")
        self.assertIn("config.yaml", str(ctx.exception))

    def test_config_loads_env_variables(self):
        """Test that config loads environment variables."""
        with patch.dict(
            os.environ,
            {
                "INFLUXDB_URL": "http://test:8086",
                "INFLUXDB_TOKEN": "test-token",
                "INFLUXDB_ORG": "test-org",
            },
        ):
            config = Config()
            self.assertEqual(config.influxdb_url, "http://test:8086")
            self.assertEqual(config.influxdb_token, "test-token")
            self.assertEqual(config.influxdb_org, "test-org")

    def test_config_required_env_values(self):
        """Test that critical env values raise ValueError when missing."""
        yaml_path = _make_temp_config()
        try:
            with patch("src.common.config.load_dotenv"):
                with patch.dict(os.environ, {}, clear=True):
                    config = Config(config_path=yaml_path)
                    with self.assertRaises(ValueError) as ctx:
                        _ = config.influxdb_url
                    self.assertIn("INFLUXDB_URL", str(ctx.exception))

                    with self.assertRaises(ValueError) as ctx:
                        _ = config.influxdb_token
                    self.assertIn("INFLUXDB_TOKEN", str(ctx.exception))

                    with self.assertRaises(ValueError) as ctx:
                        _ = config.influxdb_org
                    self.assertIn("INFLUXDB_ORG", str(ctx.exception))
        finally:
            os.unlink(yaml_path)

    def test_config_bucket_properties(self):
        """Test bucket configuration properties."""
        with patch.dict(
            os.environ,
            {
                "INFLUXDB_BUCKET_TEMPERATURES": "temps_test",
                "INFLUXDB_BUCKET_WEATHER": "weather_test",
                "INFLUXDB_BUCKET_SPOTPRICE": "spotprice_test",
                "INFLUXDB_BUCKET_EMETERS": "emeters_test",
                "INFLUXDB_BUCKET_CHECKWATT": "checkwatt_test",
            },
        ):
            config = Config()
            self.assertEqual(config.influxdb_bucket_temperatures, "temps_test")
            self.assertEqual(config.influxdb_bucket_weather, "weather_test")
            self.assertEqual(config.influxdb_bucket_spotprice, "spotprice_test")
            self.assertEqual(config.influxdb_bucket_emeters, "emeters_test")
            self.assertEqual(config.influxdb_bucket_checkwatt, "checkwatt_test")

    def test_config_hardware_from_yaml(self):
        """Test hardware configuration comes from config.yaml."""
        yaml_content = (
            MINIMAL_CONFIG_YAML.replace("pump_i2c_bus: 1", "pump_i2c_bus: 2")
            .replace("pump_i2c_address: 0x10", "pump_i2c_address: 0x20")
            .replace(
                "shelly_relay_url: http://192.168.1.5",
                "shelly_relay_url: http://192.168.1.10",
            )
        )
        yaml_path = _make_temp_config(yaml_content)
        try:
            config = Config(config_path=yaml_path)
            self.assertEqual(config.pump_i2c_bus, 2)
            self.assertEqual(config.pump_i2c_address, 0x20)
            self.assertEqual(config.shelly_relay_url, "http://192.168.1.10")
        finally:
            os.unlink(yaml_path)

    def test_config_hardware_required(self):
        """Test that missing hardware config raises ValueError."""
        yaml_content = """
heating:
  curve:
    -20: 10
    0: 6
    16: 2
  evuoff_threshold_price: 0.30
  evuoff_max_continuous_hours: 3
data_collection:
  spot_prices:
    value_added_tax: 1.255
    sellers_margin: 0.50
    production_buyback_margin: 0.30
    transfer_day_price: 2.59
    transfer_night_price: 1.35
    transfer_tax_price: 2.79372
"""
        yaml_path = _make_temp_config(yaml_content)
        try:
            config = Config(config_path=yaml_path)
            with self.assertRaises(ValueError) as ctx:
                _ = config.pump_i2c_bus
            self.assertIn("hardware.pump_i2c_bus", str(ctx.exception))
        finally:
            os.unlink(yaml_path)

    def test_config_heating_curve_from_yaml(self):
        """Test heating curve comes from config.yaml."""
        yaml_content = (
            MINIMAL_CONFIG_YAML.replace("-20: 10", "-20: 14")
            .replace("0: 6", "0: 8")
            .replace("16: 2", "16: 3")
        )
        yaml_path = _make_temp_config(yaml_content)
        try:
            config = Config(config_path=yaml_path)
            curve = config.heating_curve
            self.assertEqual(curve[-20], 14.0)
            self.assertEqual(curve[0], 8.0)
            self.assertEqual(curve[16], 3.0)
        finally:
            os.unlink(yaml_path)

    def test_config_heating_curve_types(self):
        """Test heating curve returns dict with int keys and float values."""
        config = Config()
        curve = config.heating_curve
        self.assertIsInstance(curve, dict)
        for key, value in curve.items():
            self.assertIsInstance(key, int)
            self.assertIsInstance(value, float)

    def test_config_evuoff_from_yaml(self):
        """Test EVU-OFF config comes from config.yaml."""
        yaml_path = _make_temp_config()
        try:
            config = Config(config_path=yaml_path)
            self.assertEqual(config.evuoff_threshold_price, 0.30)
            self.assertEqual(config.evuoff_max_continuous_hours, 3)
        finally:
            os.unlink(yaml_path)

    def test_config_spot_prices_from_yaml(self):
        """Test spot price config comes from config.yaml."""
        yaml_path = _make_temp_config()
        try:
            config = Config(config_path=yaml_path)
            spot_cfg = config.spot_prices_config
            self.assertEqual(spot_cfg["value_added_tax"], 1.255)
            self.assertEqual(spot_cfg["sellers_margin"], 0.50)
            self.assertEqual(spot_cfg["transfer_day_price"], 2.59)
        finally:
            os.unlink(yaml_path)

    def test_config_logging_from_yaml(self):
        """Test logging config comes from config.yaml with sensible defaults."""
        yaml_content = MINIMAL_CONFIG_YAML.replace("level: INFO", "level: DEBUG").replace(
            "dir: /var/log/redhouse", "dir: /tmp/test-logs"
        )
        yaml_path = _make_temp_config(yaml_content)
        try:
            config = Config(config_path=yaml_path)
            self.assertEqual(config.log_level, "DEBUG")
            self.assertEqual(config.log_dir, "/tmp/test-logs")
            self.assertEqual(config.log_max_bytes, 10485760)
            self.assertEqual(config.log_backup_count, 5)
        finally:
            os.unlink(yaml_path)

    def test_config_logging_defaults(self):
        """Test logging has sensible defaults if not in config.yaml."""
        yaml_content = """
heating:
  curve:
    -20: 10
    0: 6
    16: 2
  evuoff_threshold_price: 0.30
  evuoff_max_continuous_hours: 3
hardware:
  pump_i2c_bus: 1
  pump_i2c_address: 0x10
  shelly_relay_url: http://192.168.1.5
  shelly_em3_url: http://192.168.1.5
data_collection:
  spot_prices:
    value_added_tax: 1.255
    sellers_margin: 0.50
    production_buyback_margin: 0.30
    transfer_day_price: 2.59
    transfer_night_price: 1.35
    transfer_tax_price: 2.79372
"""
        yaml_path = _make_temp_config(yaml_content)
        try:
            config = Config(config_path=yaml_path)
            self.assertEqual(config.log_level, "INFO")
            self.assertEqual(config.log_dir, "/var/log/redhouse")
            self.assertEqual(config.log_max_bytes, 10485760)
            self.assertEqual(config.log_backup_count, 5)
        finally:
            os.unlink(yaml_path)

    def test_config_sensor_mapping(self):
        """Test sensor mapping loads from sensors.yaml."""
        yaml_path = _make_temp_config()
        sensors_path = yaml_path.replace(".yaml", "_sensors.yaml")
        # Create sensors.yaml next to config.yaml
        sensors_dir = os.path.dirname(yaml_path)
        sensors_file = os.path.join(sensors_dir, os.path.basename(sensors_path))
        with open(sensors_file, "w") as f:
            f.write('sensor_mapping:\n  "28-abc": "TestRoom"\n')

        try:
            # Config looks for sensors.yaml in same dir as config.yaml
            # We need to place it as "sensors.yaml" in the same directory
            actual_sensors = os.path.join(sensors_dir, "sensors.yaml")
            os.rename(sensors_file, actual_sensors)
            config = Config(config_path=yaml_path)
            self.assertEqual(config.sensor_mapping["28-abc"], "TestRoom")
        finally:
            os.unlink(yaml_path)
            if os.path.exists(actual_sensors):
                os.unlink(actual_sensors)

    def test_config_sensor_mapping_empty_without_file(self):
        """Test sensor mapping is empty dict when sensors.yaml doesn't exist."""
        yaml_path = _make_temp_config()
        try:
            config = Config(config_path=yaml_path)
            self.assertEqual(config.sensor_mapping, {})
        finally:
            os.unlink(yaml_path)

    def test_get_config_singleton(self):
        """Test that get_config returns singleton instance."""
        import src.common.config

        src.common.config._config = None

        config1 = get_config()
        config2 = get_config()
        self.assertIs(config1, config2)

    def test_config_get_method(self):
        """Test generic get method with dot notation."""
        yaml_content = (
            MINIMAL_CONFIG_YAML
            + """
custom:
  nested:
    value: 42
"""
        )
        yaml_path = _make_temp_config(yaml_content)
        try:
            config = Config(config_path=yaml_path)
            value = config.get("custom.nested.value")
            self.assertEqual(value, 42)
        finally:
            os.unlink(yaml_path)

    def test_config_get_with_default(self):
        """Test get method returns default when key not found."""
        config = Config()
        value = config.get("nonexistent.key", "default_value")
        self.assertEqual(value, "default_value")

    def test_config_weather_latlon_from_env(self):
        """Test weather location comes from .env (PII)."""
        with patch.dict(os.environ, {"WEATHER_LATLON": "61.0,25.0"}):
            config = Config()
            self.assertEqual(config.weather_latlon, "61.0,25.0")

    def test_config_weather_latlon_required(self):
        """Test weather location raises error when missing."""
        yaml_path = _make_temp_config()
        try:
            with patch("src.common.config.load_dotenv"):
                with patch.dict(os.environ, {}, clear=True):
                    config = Config(config_path=yaml_path)
                    with self.assertRaises(ValueError) as ctx:
                        _ = config.weather_latlon
                    self.assertIn("WEATHER_LATLON", str(ctx.exception))
        finally:
            os.unlink(yaml_path)


if __name__ == "__main__":
    unittest.main()
