"""Unit tests for analytics base class."""

import datetime
from unittest.mock import MagicMock, Mock

import pytest
import pytz

from src.aggregation.analytics_base import AnalyticsAggregatorBase
from src.common.influx_client import InfluxClient


class ConcreteAnalyticsAggregator(AnalyticsAggregatorBase):
    """Concrete implementation for testing the base class."""

    INTERVAL_SECONDS = 900  # 15 minutes

    def calculate_metrics(self, raw_data, window_start, window_end):
        """Dummy implementation."""
        return {"test": "metrics"}

    def write_results(self, metrics, timestamp):
        """Dummy implementation."""
        return True


@pytest.fixture
def mock_influx_client():
    """Create a mock InfluxDB client."""
    client = MagicMock(spec=InfluxClient)
    client.query_api = MagicMock()
    client.write_api = MagicMock()
    return client


@pytest.fixture
def config():
    """Get mock configuration."""
    mock_config = MagicMock()
    mock_config.influxdb_org = "test_org"
    mock_config.influxdb_bucket_emeters_5min = "test_emeters_5min"
    mock_config.influxdb_bucket_spotprice = "test_spotprice"
    mock_config.influxdb_bucket_weather = "test_weather"
    mock_config.influxdb_bucket_temperatures = "test_temperatures"
    mock_config.influxdb_bucket_analytics_15min = "test_analytics_15min"
    mock_config.influxdb_bucket_analytics_1hour = "test_analytics_1hour"
    return mock_config


@pytest.fixture
def aggregator(mock_influx_client, config):
    """Create a concrete analytics aggregator instance."""
    return ConcreteAnalyticsAggregator(mock_influx_client, config)


@pytest.fixture
def time_window():
    """Create a test time window."""
    tz = pytz.timezone("Europe/Helsinki")
    window_start = tz.localize(datetime.datetime(2026, 1, 8, 10, 0, 0))
    window_end = tz.localize(datetime.datetime(2026, 1, 8, 10, 15, 0))
    return window_start, window_end


class TestAnalyticsAggregatorBase:
    """Test the AnalyticsAggregatorBase class."""

    def test_initialization(self, aggregator, mock_influx_client, config):
        """Test that aggregator is initialized correctly."""
        assert aggregator.influx == mock_influx_client
        assert aggregator.config == config
        assert aggregator.INTERVAL_SECONDS == 900

    def test_fetch_emeters_5min_data_success(self, aggregator, time_window):
        """Test successful fetch of emeters_5min data."""
        window_start, window_end = time_window

        # Mock InfluxDB response
        mock_record = Mock()
        mock_record.get_time.return_value = window_start
        mock_record.values = {
            "solar_yield_avg": 2000.0,
            "solar_yield_diff": 166.67,
            "consumption_avg": 3000.0,
            "consumption_diff": 250.0,
            "emeter_avg": 1000.0,
            "emeter_diff": 83.33,
            "battery_charge_avg": 0.0,
            "battery_charge_diff": 0.0,
            "battery_discharge_avg": 0.0,
            "battery_discharge_diff": 0.0,
            "Battery_SoC": 65.0,
            "energy_import_avg": 1000.0,
            "energy_export_avg": 0.0,
        }

        mock_table = Mock()
        mock_table.records = [mock_record]

        aggregator.influx.query_api.query.return_value = [mock_table]

        data = aggregator._fetch_emeters_5min_data(window_start, window_end)

        assert len(data) == 1
        assert data[0]["solar_yield_avg"] == 2000.0
        assert data[0]["consumption_avg"] == 3000.0
        assert data[0]["Battery_SoC"] == 65.0

    def test_fetch_emeters_5min_data_empty(self, aggregator, time_window):
        """Test fetch of emeters_5min data with no results."""
        window_start, window_end = time_window
        aggregator.influx.query_api.query.return_value = []

        data = aggregator._fetch_emeters_5min_data(window_start, window_end)

        assert data == []

    def test_fetch_emeters_5min_data_exception(self, aggregator, time_window):
        """Test fetch of emeters_5min data with exception."""
        window_start, window_end = time_window
        aggregator.influx.query_api.query.side_effect = Exception("Database error")

        data = aggregator._fetch_emeters_5min_data(window_start, window_end)

        assert data == []

    def test_fetch_spotprice_data_success(self, aggregator, time_window):
        """Test successful fetch of spot price data."""
        window_start, window_end = time_window

        # Mock InfluxDB response
        mock_record = Mock()
        mock_record.values = {"price_total": 8.5, "price_sell": 4.0}

        mock_table = Mock()
        mock_table.records = [mock_record]

        aggregator.influx.query_api.query.return_value = [mock_table]

        spotprice = aggregator._fetch_spotprice_data(window_end)

        assert spotprice is not None
        assert spotprice["price_total"] == 8.5
        assert spotprice["price_sell"] == 4.0

    def test_fetch_spotprice_data_empty(self, aggregator, time_window):
        """Test fetch of spot price data with no results."""
        window_start, window_end = time_window
        aggregator.influx.query_api.query.return_value = []

        spotprice = aggregator._fetch_spotprice_data(window_end)

        assert spotprice is None

    def test_fetch_spotprice_data_exception(self, aggregator, time_window):
        """Test fetch of spot price data with exception."""
        window_start, window_end = time_window
        aggregator.influx.query_api.query.side_effect = Exception("Database error")

        spotprice = aggregator._fetch_spotprice_data(window_end)

        assert spotprice is None

    def test_fetch_weather_data_success(self, aggregator, time_window):
        """Test successful fetch of weather data."""
        window_start, window_end = time_window

        # Mock InfluxDB response with multiple records
        mock_records = []
        for field_name, value in [
            ("air_temperature", 5.5),
            ("cloud_cover", 50.0),
            ("solar_radiation", 150.0),
            ("wind_speed", 4.5),
        ]:
            mock_record = Mock()
            mock_record.get_field.return_value = field_name
            mock_record.get_value.return_value = value
            mock_records.append(mock_record)

        mock_table = Mock()
        mock_table.records = mock_records

        aggregator.influx.query_api.query.return_value = [mock_table]

        weather = aggregator._fetch_weather_data(window_start, window_end)

        assert weather is not None
        assert weather["air_temperature"] == 5.5
        assert weather["cloud_cover"] == 50.0
        assert weather["solar_radiation"] == 150.0
        assert weather["wind_speed"] == 4.5

    def test_fetch_weather_data_empty(self, aggregator, time_window):
        """Test fetch of weather data with no results."""
        window_start, window_end = time_window
        aggregator.influx.query_api.query.return_value = []

        weather = aggregator._fetch_weather_data(window_start, window_end)

        assert weather is None

    def test_fetch_weather_data_exception(self, aggregator, time_window):
        """Test fetch of weather data with exception."""
        window_start, window_end = time_window
        aggregator.influx.query_api.query.side_effect = Exception("Database error")

        weather = aggregator._fetch_weather_data(window_start, window_end)

        assert weather is None

    def test_fetch_temperatures_data_success(self, aggregator, time_window):
        """Test successful fetch of temperature data."""
        window_start, window_end = time_window

        # Mock InfluxDB response with multiple records
        mock_records = []
        for field_name, value in [("PaaMH", 50.0), ("Ulkolampo", 5.0), ("PalMH", 40.0)]:
            mock_record = Mock()
            mock_record.get_field.return_value = field_name
            mock_record.get_value.return_value = value
            mock_records.append(mock_record)

        mock_table = Mock()
        mock_table.records = mock_records

        aggregator.influx.query_api.query.return_value = [mock_table]

        temperatures = aggregator._fetch_temperatures_data(window_start, window_end)

        assert temperatures is not None
        assert temperatures["PaaMH"] == 50.0
        assert temperatures["Ulkolampo"] == 5.0
        assert temperatures["PalMH"] == 40.0

    def test_fetch_temperatures_data_empty(self, aggregator, time_window):
        """Test fetch of temperature data with no results."""
        window_start, window_end = time_window
        aggregator.influx.query_api.query.return_value = []

        temperatures = aggregator._fetch_temperatures_data(window_start, window_end)

        assert temperatures is None

    def test_fetch_temperatures_data_exception(self, aggregator, time_window):
        """Test fetch of temperature data with exception."""
        window_start, window_end = time_window
        aggregator.influx.query_api.query.side_effect = Exception("Database error")

        temperatures = aggregator._fetch_temperatures_data(window_start, window_end)

        assert temperatures is None

    def test_fetch_data_orchestration(self, aggregator, time_window):
        """Test fetch_data orchestrates all fetch methods."""
        window_start, window_end = time_window

        # Mock all fetch methods
        aggregator._fetch_emeters_5min_data = MagicMock(return_value=[{"test": "emeters"}])
        aggregator._fetch_spotprice_data = MagicMock(return_value={"test": "spotprice"})
        aggregator._fetch_weather_data = MagicMock(return_value={"test": "weather"})
        aggregator._fetch_temperatures_data = MagicMock(return_value={"test": "temperatures"})

        raw_data = aggregator.fetch_data(window_start, window_end)

        assert raw_data["emeters"] == [{"test": "emeters"}]
        assert raw_data["spotprice"] == {"test": "spotprice"}
        assert raw_data["weather"] == {"test": "weather"}
        assert raw_data["temperatures"] == {"test": "temperatures"}

        aggregator._fetch_emeters_5min_data.assert_called_once_with(window_start, window_end)
        aggregator._fetch_spotprice_data.assert_called_once_with(window_end)
        aggregator._fetch_weather_data.assert_called_once_with(window_start, window_end)
        aggregator._fetch_temperatures_data.assert_called_once_with(window_start, window_end)

    def test_validate_data_with_emeters(self, aggregator):
        """Test validation with emeters data."""
        raw_data = {"emeters": [{"test": "data"}]}
        assert aggregator.validate_data(raw_data) is True

    def test_validate_data_without_emeters(self, aggregator):
        """Test validation without emeters data."""
        raw_data = {"emeters": []}
        assert aggregator.validate_data(raw_data) is False

    def test_validate_data_missing_key(self, aggregator):
        """Test validation with missing emeters key."""
        raw_data = {}
        assert aggregator.validate_data(raw_data) is False

    def test_calculate_cost_allocation(self, aggregator):
        """Test cost allocation calculation."""
        metrics = {
            "solar_yield_sum": 2000.0,
            "consumption_sum": 3000.0,
            "battery_charge_sum": 0.0,
            "battery_discharge_sum": 0.0,
            "export_sum": 0.0,
        }
        spotprice = {"price_total": 8.5, "price_sell": 4.0}

        cost_metrics = aggregator._calculate_cost_allocation(metrics, spotprice)

        assert "solar_to_consumption" in cost_metrics
        assert "solar_direct_value" in cost_metrics
        assert cost_metrics["solar_to_consumption"] == 2000.0
        assert cost_metrics["solar_direct_value"] == pytest.approx(0.17, rel=0.01)

    def test_calculate_cost_allocation_missing_prices(self, aggregator):
        """Test cost allocation with missing price data."""
        metrics = {"solar_yield_sum": 2000.0, "consumption_sum": 3000.0}
        spotprice = {"price_total": None, "price_sell": 4.0}

        cost_metrics = aggregator._calculate_cost_allocation(metrics, spotprice)

        assert cost_metrics == {}

    def test_calculate_self_consumption(self, aggregator):
        """Test self-consumption calculation."""
        metrics = {
            "solar_yield_sum": 2000.0,
            "battery_charge_sum": 0.0,
            "export_sum": 500.0,
            "solar_to_consumption": 1500.0,
        }

        self_consumption = aggregator._calculate_self_consumption(metrics)

        assert self_consumption["solar_direct_sum"] == 1500.0
        assert self_consumption["self_consumption_ratio"] == pytest.approx(75.0, rel=0.01)

    def test_calculate_self_consumption_no_solar(self, aggregator):
        """Test self-consumption with no solar production."""
        metrics = {"solar_yield_sum": 0.0, "battery_charge_sum": 0.0, "export_sum": 0.0}

        self_consumption = aggregator._calculate_self_consumption(metrics)

        assert self_consumption["solar_direct_sum"] == 0.0
        assert self_consumption["self_consumption_ratio"] == 0.0

    def test_add_weather_and_temperature_fields(self, aggregator):
        """Test adding weather and temperature fields."""
        metrics = {}
        weather = {
            "air_temperature": 5.5,
            "cloud_cover": 50.0,
            "solar_radiation": 150.0,
            "wind_speed": 4.5,
        }
        temperatures = {"PaaMH": 50.0, "Ulkolampo": 5.0}

        aggregator._add_weather_and_temperature_fields(metrics, weather, temperatures)

        assert metrics["air_temperature"] == 5.5
        assert metrics["cloud_cover"] == 50.0
        assert metrics["PaaMH"] == 50.0
        assert metrics["Ulkolampo"] == 5.0

    def test_add_weather_and_temperature_fields_none(self, aggregator):
        """Test adding weather and temperature fields with None data."""
        metrics = {}

        aggregator._add_weather_and_temperature_fields(metrics, None, None)

        assert "air_temperature" not in metrics
        assert "PaaMH" not in metrics
