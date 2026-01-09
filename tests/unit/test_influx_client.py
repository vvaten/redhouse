"""Unit tests for InfluxDB client wrapper."""

import datetime
import os
import sys
from unittest.mock import Mock, patch

import pytest

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.common.config_validator import ConfigValidationError
from src.common.influx_client import InfluxClient


@pytest.fixture
def mock_config():
    """Create a mock configuration object."""
    config = Mock()
    config.influxdb_url = "http://localhost:8086"
    config.influxdb_token = "test-token"
    config.influxdb_org = "test-org"
    config.influxdb_bucket_temperatures = "temperatures"
    config.influxdb_bucket_weather = "weather"
    config.influxdb_bucket_spotprice = "spotprice"
    config.get = Mock(return_value={})  # For sensor_mapping
    return config


@pytest.fixture
def mock_influx_client_module():
    """Mock the influxdb_client module."""
    with patch("src.common.influx_client.influxdb_client") as mock:
        mock_client_instance = Mock()
        mock_write_api = Mock()
        mock_query_api = Mock()

        mock_client_instance.write_api.return_value = mock_write_api
        mock_client_instance.query_api.return_value = mock_query_api

        mock.InfluxDBClient.return_value = mock_client_instance

        # Mock Point to support method chaining
        mock_point = Mock()
        mock_point.field.return_value = mock_point
        mock_point.tag.return_value = mock_point
        mock_point.time.return_value = mock_point
        mock.Point.return_value = mock_point

        yield mock


@pytest.fixture
def mock_config_validator():
    """Mock ConfigValidator to avoid strict mode checks."""
    with patch("src.common.influx_client.ConfigValidator") as mock:
        mock.check_environment.return_value = ["INFO: Test environment"]
        mock.get_strict_mode.return_value = False
        mock.validate_write.return_value = None
        yield mock


class TestInfluxClientInit:
    """Tests for InfluxClient initialization."""

    def test_init_with_config(self, mock_config, mock_influx_client_module, mock_config_validator):
        """Test initialization with provided config."""
        client = InfluxClient(mock_config)

        assert client.config == mock_config
        assert client.client is not None
        assert client.write_api is not None
        assert client.query_api is not None

    def test_init_without_config(
        self, mock_influx_client_module, mock_config_validator, mock_config
    ):
        """Test initialization with default config."""
        with patch("src.common.influx_client.get_config", return_value=mock_config):
            client = InfluxClient()

            assert client.config == mock_config

    def test_init_logs_environment_info(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test that environment info is logged on initialization."""
        mock_config_validator.check_environment.return_value = [
            "INFO: Test environment",
            "WARNING: Test warning",
        ]

        with patch("src.common.influx_client.logger") as mock_logger:
            InfluxClient(mock_config)

            assert mock_logger.info.called
            assert mock_logger.warning.called


class TestWritePoint:
    """Tests for write_point method."""

    def test_write_point_basic(self, mock_config, mock_influx_client_module, mock_config_validator):
        """Test basic write_point operation."""
        client = InfluxClient(mock_config)

        success = client.write_point(
            measurement="test_measurement", fields={"temp": 21.5, "humidity": 45.0}
        )

        assert success is True
        assert client.write_api.write.called

    def test_write_point_with_tags(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test write_point with tags."""
        client = InfluxClient(mock_config)

        success = client.write_point(
            measurement="test_measurement",
            fields={"value": 100.0},
            tags={"sensor": "test1", "location": "room1"},
        )

        assert success is True

    def test_write_point_with_timestamp(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test write_point with explicit timestamp."""
        client = InfluxClient(mock_config)
        timestamp = datetime.datetime(2024, 1, 1, 12, 0, 0)

        success = client.write_point(
            measurement="test_measurement", fields={"value": 42.0}, timestamp=timestamp
        )

        assert success is True

    def test_write_point_with_custom_bucket(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test write_point with custom bucket."""
        client = InfluxClient(mock_config)

        success = client.write_point(
            measurement="test_measurement", fields={"value": 42.0}, bucket="custom_bucket"
        )

        assert success is True
        # Verify bucket parameter was used
        call_args = client.write_api.write.call_args
        assert call_args[1]["bucket"] == "custom_bucket"

    def test_write_point_validation_error(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test write_point blocks on validation error."""
        client = InfluxClient(mock_config)

        # Make validation raise an error
        mock_config_validator.validate_write.side_effect = ConfigValidationError(
            "Test validation error"
        )

        success = client.write_point(measurement="test_measurement", fields={"value": 42.0})

        assert success is False
        assert not client.write_api.write.called

    def test_write_point_with_validation_warning(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test write_point continues with validation warning."""
        client = InfluxClient(mock_config)

        # Make validation return a warning
        mock_config_validator.validate_write.return_value = "WARNING: Test warning"

        with patch("src.common.influx_client.logger") as mock_logger:
            success = client.write_point(measurement="test_measurement", fields={"value": 42.0})

            assert success is True
            assert mock_logger.warning.called

    def test_write_point_exception_handling(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test write_point handles exceptions."""
        client = InfluxClient(mock_config)

        # Make write raise an exception
        client.write_api.write.side_effect = Exception("Test exception")

        success = client.write_point(measurement="test_measurement", fields={"value": 42.0})

        assert success is False


class TestWriteTemperatures:
    """Tests for write_temperatures method."""

    def test_write_temperatures_basic(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test basic write_temperatures operation."""
        client = InfluxClient(mock_config)

        # Setup sensor mapping
        mock_config.get.return_value = {
            "28-xxxx8a": "Sensor1",
            "28-xxxxc1": "Sensor2",
        }

        temperature_data = {
            "28-xxxx8a": {"temp": 21.5, "updated": 1609459200},
            "28-xxxxc1": {"temp": 22.0, "updated": 1609459200},
        }

        success = client.write_temperatures(temperature_data)

        assert success is True
        assert client.write_api.write.called

    def test_write_temperatures_with_none_values(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test write_temperatures skips None values."""
        client = InfluxClient(mock_config)

        mock_config.get.return_value = {"sensor1": "TestSensor"}

        temperature_data = {
            "sensor1": {"temp": None, "updated": 1609459200},
            "sensor2": {"temp": 21.5},
        }

        success = client.write_temperatures(temperature_data)

        assert success is True

    def test_write_temperatures_exception_handling(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test write_temperatures handles exceptions."""
        client = InfluxClient(mock_config)

        client.write_api.write.side_effect = Exception("Test exception")

        temperature_data = {"sensor1": {"temp": 21.5}}

        success = client.write_temperatures(temperature_data)

        assert success is False


class TestWriteHumidities:
    """Tests for write_humidities method."""

    def test_write_humidities_basic(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test basic write_humidities operation."""
        client = InfluxClient(mock_config)

        mock_config.get.return_value = {"sensor1": "TestSensor"}

        humidity_data = {"sensor1": {"hum": 45.0}}

        success = client.write_humidities(humidity_data)

        assert success is True
        assert client.write_api.write.called

    def test_write_humidities_with_none_values(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test write_humidities skips None values."""
        client = InfluxClient(mock_config)

        mock_config.get.return_value = {}

        humidity_data = {"sensor1": {"hum": None}, "sensor2": {"hum": 50.0}}

        success = client.write_humidities(humidity_data)

        assert success is True

    def test_write_humidities_exception_handling(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test write_humidities handles exceptions."""
        client = InfluxClient(mock_config)

        client.write_api.write.side_effect = Exception("Test exception")

        humidity_data = {"sensor1": {"hum": 45.0}}

        success = client.write_humidities(humidity_data)

        assert success is False


class TestWriteWeather:
    """Tests for write_weather method."""

    def test_write_weather_basic(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test basic write_weather operation."""
        client = InfluxClient(mock_config)

        weather_data = {
            datetime.datetime(2024, 1, 1, 12, 0, 0): {
                "Air temperature": 15.0,
                "Wind speed": 5.0,
            },
            datetime.datetime(2024, 1, 1, 13, 0, 0): {
                "Air temperature": 16.0,
                "Wind speed": 6.0,
            },
        }

        success = client.write_weather(weather_data)

        assert success is True
        assert client.write_api.write.called
        # Check that write was called with a list of points
        call_args = client.write_api.write.call_args
        assert call_args[1]["bucket"] == "weather"

    def test_write_weather_with_none_values(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test write_weather skips None values."""
        client = InfluxClient(mock_config)

        weather_data = {
            datetime.datetime(2024, 1, 1, 12, 0, 0): {
                "Air temperature": 15.0,
                "Wind speed": None,
            }
        }

        success = client.write_weather(weather_data)

        assert success is True

    def test_write_weather_exception_handling(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test write_weather handles exceptions."""
        client = InfluxClient(mock_config)

        client.write_api.write.side_effect = Exception("Test exception")

        weather_data = {datetime.datetime(2024, 1, 1, 12, 0, 0): {"temperature": 15.0}}

        success = client.write_weather(weather_data)

        assert success is False


class TestWriteSpotPrices:
    """Tests for write_spot_prices method."""

    def test_write_spot_prices_basic(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test basic write_spot_prices operation."""
        client = InfluxClient(mock_config)

        spot_price_data = [
            {
                "epoch_timestamp": 1609459200,
                "price": 5.0,
                "price_sell": 4.0,
                "price_withtax": 6.24,
                "price_total": 6.24,
            },
            {
                "epoch_timestamp": 1609462800,
                "price": 5.5,
                "price_sell": 4.5,
                "price_withtax": 6.86,
                "price_total": 6.86,
            },
        ]

        success = client.write_spot_prices(spot_price_data)

        assert success is True
        assert client.write_api.write.called

    def test_write_spot_prices_exception_handling(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test write_spot_prices handles exceptions."""
        client = InfluxClient(mock_config)

        client.write_api.write.side_effect = Exception("Test exception")

        spot_price_data = [
            {
                "epoch_timestamp": 1609459200,
                "price": 5.0,
                "price_sell": 4.0,
                "price_withtax": 6.24,
                "price_total": 6.24,
            }
        ]

        success = client.write_spot_prices(spot_price_data)

        assert success is False


class TestConvertSensorIdToName:
    """Tests for _convert_sensor_id_to_name method."""

    def test_convert_sensor_direct_match(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test direct sensor ID lookup."""
        client = InfluxClient(mock_config)

        mock_config.get.return_value = {"28-xxxx8a": "TestSensor"}

        name = client._convert_sensor_id_to_name("28-xxxx8a")

        assert name == "TestSensor"

    def test_convert_sensor_ds18b20_suffix(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test DS18B20 sensor suffix matching."""
        client = InfluxClient(mock_config)

        mock_config.get.return_value = {"28-abcd8a": "TestSensor"}

        # Should match by last 2 characters
        name = client._convert_sensor_id_to_name("28-xxxx8a")

        assert name == "TestSensor"

    def test_convert_sensor_shelly_suffix(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test Shelly sensor suffix matching."""
        client = InfluxClient(mock_config)

        mock_config.get.return_value = {"shelly-180": "ShellySensor"}

        # Should match by last 3 characters
        name = client._convert_sensor_id_to_name("shelly-180")

        assert name == "ShellySensor"

    def test_convert_sensor_no_match(
        self, mock_config, mock_influx_client_module, mock_config_validator
    ):
        """Test sensor ID with no mapping."""
        client = InfluxClient(mock_config)

        mock_config.get.return_value = {}

        name = client._convert_sensor_id_to_name("unknown-sensor")

        assert name is None


class TestClose:
    """Tests for close method."""

    def test_close(self, mock_config, mock_influx_client_module, mock_config_validator):
        """Test close method."""
        client = InfluxClient(mock_config)

        client.close()

        assert client.client.close.called
