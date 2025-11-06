"""Unit tests for configuration validation."""

import unittest
from unittest.mock import Mock

from src.common.config_validator import ConfigValidationError, ConfigValidator


class TestConfigValidator(unittest.TestCase):
    """Test configuration validation functions."""

    def test_is_production_bucket(self):
        """Test production bucket detection."""
        self.assertTrue(ConfigValidator.is_production_bucket("temperatures"))
        self.assertTrue(ConfigValidator.is_production_bucket("weather"))
        self.assertFalse(ConfigValidator.is_production_bucket("temperatures_test"))
        self.assertFalse(ConfigValidator.is_production_bucket("custom_bucket"))

    def test_is_test_bucket(self):
        """Test test bucket detection."""
        self.assertTrue(ConfigValidator.is_test_bucket("temperatures_test"))
        self.assertTrue(ConfigValidator.is_test_bucket("weather_test"))
        self.assertFalse(ConfigValidator.is_test_bucket("temperatures"))
        self.assertFalse(ConfigValidator.is_test_bucket("custom"))

    def test_validate_field_names_real_sensors(self):
        """Test validation with real sensor names."""
        fields = {"PaaMH": 21.5, "Ulkolampo": 5.2, "Keittio": 22.0}
        test_fields = ConfigValidator.validate_field_names(fields)
        self.assertEqual(len(test_fields), 0)

    def test_validate_field_names_test_sensors(self):
        """Test validation with test sensor names."""
        fields = {"TestSensor1": 21.5, "TestSensor2": 22.0}
        test_fields = ConfigValidator.validate_field_names(fields, allow_test_fields=True)
        self.assertEqual(len(test_fields), 2)
        self.assertIn("TestSensor1", test_fields)
        self.assertIn("TestSensor2", test_fields)

    def test_validate_field_names_blocks_test_sensors(self):
        """Test validation blocks test sensors when not allowed."""
        fields = {"TestSensor1": 21.5, "RealSensor": 22.0}
        with self.assertRaises(ConfigValidationError) as ctx:
            ConfigValidator.validate_field_names(fields, allow_test_fields=False)
        self.assertIn("TestSensor1", str(ctx.exception))

    def test_validate_write_test_to_test_bucket(self):
        """Test writing test data to test bucket (should be OK)."""
        fields = {"TestSensor1": 21.5}
        warning = ConfigValidator.validate_write(
            bucket="temperatures_test", fields=fields, strict_mode=False
        )
        # Should return warning but not raise
        self.assertIsNotNone(warning)
        self.assertIn("test", warning.lower())

    def test_validate_write_real_to_production_bucket(self):
        """Test writing real data to production bucket (should warn)."""
        fields = {"PaaMH": 21.5, "Ulkolampo": 5.2}
        warning = ConfigValidator.validate_write(
            bucket="temperatures", fields=fields, strict_mode=False
        )
        # Should return warning
        self.assertIsNotNone(warning)
        self.assertIn("PRODUCTION", warning)

    def test_validate_write_test_to_production_blocked(self):
        """Test writing test data to production bucket (should block)."""
        fields = {"TestSensor1": 21.5, "PaaMH": 22.0}
        with self.assertRaises(ConfigValidationError) as ctx:
            ConfigValidator.validate_write(bucket="temperatures", fields=fields, strict_mode=False)
        self.assertIn("TestSensor1", str(ctx.exception))
        self.assertIn("production", str(ctx.exception).lower())

    def test_validate_write_strict_mode_blocks_production(self):
        """Test strict mode blocks all writes to production."""
        fields = {"RealSensor": 21.5}
        with self.assertRaises(ConfigValidationError) as ctx:
            ConfigValidator.validate_write(bucket="temperatures", fields=fields, strict_mode=True)
        self.assertIn("Strict mode", str(ctx.exception))

    def test_check_environment_production(self):
        """Test environment check with production buckets."""
        config = Mock()
        config.influxdb_bucket_temperatures = "temperatures"
        config.influxdb_bucket_weather = "weather"
        config.influxdb_bucket_spotprice = "spotprice"
        config.influxdb_bucket_emeters = "emeters"
        config.influxdb_bucket_checkwatt = "checkwatt_full_data"

        messages = ConfigValidator.check_environment(config)

        self.assertTrue(any("PRODUCTION" in msg for msg in messages))
        self.assertTrue(any("temperatures" in msg for msg in messages))

    def test_check_environment_test(self):
        """Test environment check with test buckets."""
        config = Mock()
        config.influxdb_bucket_temperatures = "temperatures_test"
        config.influxdb_bucket_weather = "weather_test"
        config.influxdb_bucket_spotprice = "spotprice_test"
        config.influxdb_bucket_emeters = "emeters_test"
        config.influxdb_bucket_checkwatt = "checkwatt_full_data_test"

        messages = ConfigValidator.check_environment(config)

        self.assertTrue(any("TEST" in msg for msg in messages))
        self.assertFalse(any("WARNING" in msg for msg in messages))

    def test_check_environment_mixed(self):
        """Test environment check with mixed buckets (should warn)."""
        config = Mock()
        config.influxdb_bucket_temperatures = "temperatures"  # Production
        config.influxdb_bucket_weather = "weather_test"  # Test
        config.influxdb_bucket_spotprice = "spotprice"  # Production
        config.influxdb_bucket_emeters = "emeters_test"  # Test
        config.influxdb_bucket_checkwatt = "checkwatt_full_data"  # Production

        messages = ConfigValidator.check_environment(config)

        self.assertTrue(any("WARNING" in msg and "Mixed" in msg for msg in messages))

    def test_require_test_environment_pass(self):
        """Test require_test_environment with all test buckets."""
        config = Mock()
        config.influxdb_bucket_temperatures = "temperatures_test"
        config.influxdb_bucket_weather = "weather_test"
        config.influxdb_bucket_spotprice = "spotprice_test"
        config.influxdb_bucket_emeters = "emeters_test"
        config.influxdb_bucket_checkwatt = "checkwatt_full_data_test"

        # Should not raise
        ConfigValidator.require_test_environment(config)

    def test_require_test_environment_fail(self):
        """Test require_test_environment with production bucket."""
        config = Mock()
        config.influxdb_bucket_temperatures = "temperatures"  # Production!
        config.influxdb_bucket_weather = "weather_test"
        config.influxdb_bucket_spotprice = "spotprice_test"
        config.influxdb_bucket_emeters = "emeters_test"
        config.influxdb_bucket_checkwatt = "checkwatt_full_data_test"

        with self.assertRaises(ConfigValidationError) as ctx:
            ConfigValidator.require_test_environment(config)
        self.assertIn("temperatures", str(ctx.exception))


    def test_staging_mode_blocks_production_writes(self):
        """Test that staging mode blocks writes to production buckets."""
        import os

        # Set staging mode
        old_val = os.environ.get("STAGING_MODE")
        os.environ["STAGING_MODE"] = "true"

        try:
            # Attempt to write to production bucket in staging mode
            with self.assertRaises(ConfigValidationError) as ctx:
                ConfigValidator.validate_write(
                    bucket="temperatures",  # Production bucket
                    fields={"test": 1.0},
                    strict_mode=False
                )

            self.assertIn("STAGING MODE", str(ctx.exception))
            self.assertIn("PRODUCTION bucket", str(ctx.exception))

        finally:
            # Restore original value
            if old_val is None:
                os.environ.pop("STAGING_MODE", None)
            else:
                os.environ["STAGING_MODE"] = old_val

    def test_staging_mode_allows_staging_writes(self):
        """Test that staging mode allows writes to staging buckets."""
        import os

        # Set staging mode
        old_val = os.environ.get("STAGING_MODE")
        os.environ["STAGING_MODE"] = "true"

        try:
            # Should NOT raise - writing to staging bucket is OK
            warning = ConfigValidator.validate_write(
                bucket="temperatures_staging",  # Staging bucket
                fields={"test": 1.0},
                strict_mode=False
            )
            # Should succeed (no exception)

        finally:
            # Restore original value
            if old_val is None:
                os.environ.pop("STAGING_MODE", None)
            else:
                os.environ["STAGING_MODE"] = old_val


if __name__ == "__main__":
    unittest.main()
