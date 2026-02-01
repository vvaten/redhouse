"""Unit tests for 1-hour analytics aggregation."""

import datetime
from unittest.mock import MagicMock

import pytest
import pytz

from src.aggregation.analytics_1hour import Analytics1HourAggregator
from src.common.config import get_config
from src.common.influx_client import InfluxClient


@pytest.fixture
def mock_influx_client():
    """Create a mock InfluxDB client."""
    client = MagicMock(spec=InfluxClient)
    client.query_api = MagicMock()
    client.write_api = MagicMock()
    client.write_point = MagicMock(return_value=True)
    return client


@pytest.fixture
def config():
    """Get configuration."""
    return get_config()


@pytest.fixture
def aggregator(mock_influx_client, config):
    """Create an Analytics1HourAggregator instance."""
    return Analytics1HourAggregator(mock_influx_client, config)


@pytest.fixture
def sample_emeters_5min_data():
    """Sample emeters_5min data for testing (12 data points = 1 hour)."""
    base_time = datetime.datetime(2026, 1, 8, 10, 0, 0, tzinfo=pytz.UTC)
    return [
        {
            "time": base_time + datetime.timedelta(minutes=i * 5),
            "solar_yield_avg": 2000.0 + i * 50,  # W, increases each interval
            "solar_yield_diff": 166.67,  # Wh per 5 min
            "consumption_avg": 3000.0,  # W, constant
            "consumption_diff": 250.0,  # Wh per 5 min
            "emeter_avg": 1000.0,  # W
            "emeter_diff": 83.33,  # Wh per 5 min
            "battery_charge_avg": 0.0,
            "battery_charge_diff": 0.0,
            "battery_discharge_avg": 0.0,
            "battery_discharge_diff": 0.0,
            "Battery_SoC": 65.0 + i,
            "energy_import_avg": 1000.0,  # W
            "energy_export_avg": 0.0,
        }
        for i in range(12)
    ]


@pytest.fixture
def sample_spotprice():
    """Sample spot price data."""
    return {"price_total": 8.5, "price_sell": 4.0}


@pytest.fixture
def sample_weather():
    """Sample weather data."""
    return {
        "air_temperature": 5.5,
        "cloud_cover": 50.0,
        "solar_radiation": 150.0,
        "wind_speed": 4.5,
    }


@pytest.fixture
def sample_temperatures():
    """Sample temperature data."""
    return {"PaaMH": 50.0, "Ulkolampo": 5.0, "PalMH": 40.0}


@pytest.fixture
def time_window():
    """Create a test time window."""
    tz = pytz.timezone("Europe/Helsinki")
    window_start = tz.localize(datetime.datetime(2026, 1, 8, 10, 0, 0))
    window_end = tz.localize(datetime.datetime(2026, 1, 8, 11, 0, 0))
    return window_start, window_end


class TestAnalytics1HourAggregator:
    """Test the Analytics1HourAggregator class."""

    def test_initialization(self, aggregator, mock_influx_client, config):
        """Test that aggregator is initialized correctly."""
        assert aggregator.influx == mock_influx_client
        assert aggregator.config == config
        assert aggregator.INTERVAL_SECONDS == 3600

    def test_validate_data_with_emeters(self, aggregator, sample_emeters_5min_data):
        """Test validation with emeters data."""
        raw_data = {
            "emeters": sample_emeters_5min_data,
            "spotprice": None,
            "weather": None,
            "temperatures": None,
        }
        assert aggregator.validate_data(raw_data) is True

    def test_validate_data_no_emeters(self, aggregator):
        """Test validation with no emeters data."""
        raw_data = {"emeters": [], "spotprice": None, "weather": None, "temperatures": None}
        assert aggregator.validate_data(raw_data) is False

    def test_calculate_energy_metrics(self, aggregator, sample_emeters_5min_data):
        """Test energy metrics calculation."""
        metrics = aggregator._calculate_energy_metrics(sample_emeters_5min_data)

        assert "solar_yield_avg" in metrics
        assert "consumption_avg" in metrics
        assert "solar_yield_sum" in metrics
        assert "consumption_sum" in metrics
        assert "Battery_SoC" in metrics

        # Check averaged power: (2000 + 2050 + ... + 2550) / 12 = 2275 W
        assert metrics["solar_yield_avg"] == pytest.approx(2275.0, rel=0.01)

        # Check summed energy: 12 x 166.67 Wh = 2000 Wh
        assert metrics["solar_yield_sum"] == pytest.approx(2000.04, rel=0.01)

        # Check consumption: 12 x 250 = 3000 Wh
        assert metrics["consumption_sum"] == pytest.approx(3000.0, rel=0.01)

        # Check battery SoC is last value
        assert metrics["Battery_SoC"] == 76.0

    def test_calculate_peak_power(self, aggregator):
        """Test peak power calculation."""
        emeters_data = [
            {
                "solar_yield_avg": 1000.0 + i * 200,  # Increases each interval
                "consumption_avg": 2000.0 + i * 100,  # Increases each interval
                "emeter_avg": 500.0 + i * 50,  # Increases each interval
            }
            for i in range(12)
        ]

        peak_metrics = aggregator._calculate_peak_power(emeters_data)

        # Peak solar: 1000 + 11*200 = 3200 W
        assert peak_metrics["solar_yield_max"] == 3200.0

        # Peak consumption: 2000 + 11*100 = 3100 W
        assert peak_metrics["consumption_max"] == 3100.0

        # Peak grid power: 500 + 11*50 = 1050 W
        assert peak_metrics["grid_power_max"] == 1050.0

    def test_calculate_peak_power_with_none_values(self, aggregator):
        """Test peak power calculation handles None values."""
        emeters_data = [
            {
                "solar_yield_avg": None if i == 0 else 1000.0 + i * 200,
                "consumption_avg": 2000.0,
                "emeter_avg": 500.0,
            }
            for i in range(12)
        ]

        peak_metrics = aggregator._calculate_peak_power(emeters_data)

        # Should handle None values (treated as 0)
        # Peak solar: 1000 + 11*200 = 3200 W (None is treated as 0)
        assert peak_metrics["solar_yield_max"] == 3200.0

    def test_calculate_cost_allocation(self, aggregator, sample_spotprice):
        """Test cost allocation calculation."""
        metrics = {
            "solar_yield_sum": 2000.0,  # Wh
            "consumption_sum": 3000.0,
            "battery_charge_sum": 0.0,
            "battery_discharge_sum": 0.0,
            "export_sum": 0.0,
        }

        cost_metrics = aggregator._calculate_cost_allocation(metrics, sample_spotprice)

        assert "solar_to_consumption" in cost_metrics
        assert "solar_direct_value" in cost_metrics
        assert "grid_import_cost" in cost_metrics

        # Solar to consumption: min(2000, 3000) = 2000 Wh
        assert cost_metrics["solar_to_consumption"] == pytest.approx(2000.0, rel=0.01)

        # Solar direct value: (2000 / 1000) * (8.5 / 100) = 0.17 EUR
        assert cost_metrics["solar_direct_value"] == pytest.approx(0.17, rel=0.01)

    def test_calculate_cost_allocation_with_export(self, aggregator, sample_spotprice):
        """Test cost allocation with solar export."""
        metrics = {
            "solar_yield_sum": 3600.0,  # Wh
            "consumption_sum": 1200.0,
            "battery_charge_sum": 0.0,
            "battery_discharge_sum": 0.0,
            "export_sum": 2400.0,
        }

        cost_metrics = aggregator._calculate_cost_allocation(metrics, sample_spotprice)

        # Solar to consumption: min(3600, 1200) = 1200 Wh
        assert cost_metrics["solar_to_consumption"] == pytest.approx(1200.0, rel=0.01)

        # Solar to export: 3600 - 1200 - 0 = 2400 Wh
        assert cost_metrics["solar_to_export"] == pytest.approx(2400.0, rel=0.01)

        # Solar export revenue: (2400 / 1000) * (4.0 / 100) = 0.096 EUR
        assert cost_metrics["solar_export_revenue"] == pytest.approx(0.096, rel=0.01)

    def test_calculate_self_consumption(self, aggregator):
        """Test self-consumption ratio calculation."""
        metrics = {
            "solar_yield_sum": 2000.0,
            "battery_charge_sum": 0.0,
            "export_sum": 500.0,
            "solar_to_consumption": 1500.0,
        }

        self_consumption_metrics = aggregator._calculate_self_consumption(metrics)

        assert "solar_direct_sum" in self_consumption_metrics
        assert "self_consumption_ratio" in self_consumption_metrics

        # Solar direct = solar_to_consumption = 1500 Wh
        assert self_consumption_metrics["solar_direct_sum"] == pytest.approx(1500.0, rel=0.01)

        # Self-consumption ratio = 1500 / 2000 * 100 = 75%
        assert self_consumption_metrics["self_consumption_ratio"] == pytest.approx(75.0, rel=0.01)

    def test_calculate_self_consumption_no_solar(self, aggregator):
        """Test self-consumption ratio when there's no solar production."""
        metrics = {"solar_yield_sum": 0.0, "battery_charge_sum": 0.0, "export_sum": 0.0}

        self_consumption_metrics = aggregator._calculate_self_consumption(metrics)

        assert self_consumption_metrics["solar_direct_sum"] == 0.0
        assert self_consumption_metrics["self_consumption_ratio"] == 0.0

    def test_add_weather_and_temperature_fields(
        self, aggregator, sample_weather, sample_temperatures
    ):
        """Test adding weather and temperature fields to metrics."""
        metrics = {}

        aggregator._add_weather_and_temperature_fields(metrics, sample_weather, sample_temperatures)

        # Check weather fields
        assert metrics["air_temperature"] == 5.5
        assert metrics["cloud_cover"] == 50.0
        assert metrics["solar_radiation"] == 150.0
        assert metrics["wind_speed"] == 4.5

        # Check temperature fields
        assert metrics["PaaMH"] == 50.0
        assert metrics["Ulkolampo"] == 5.0
        assert metrics["PalMH"] == 40.0

    def test_calculate_metrics(
        self,
        aggregator,
        sample_emeters_5min_data,
        sample_spotprice,
        sample_weather,
        sample_temperatures,
        time_window,
    ):
        """Test full metrics calculation."""
        window_start, window_end = time_window
        raw_data = {
            "emeters": sample_emeters_5min_data,
            "spotprice": sample_spotprice,
            "weather": sample_weather,
            "temperatures": sample_temperatures,
        }

        metrics = aggregator.calculate_metrics(raw_data, window_start, window_end)

        assert metrics is not None

        # Energy metrics
        assert "solar_yield_avg" in metrics
        assert "consumption_avg" in metrics

        # Peak power metrics (1-hour specific)
        assert "consumption_max" in metrics
        assert "solar_yield_max" in metrics
        assert "grid_power_max" in metrics

        # Cost metrics
        assert "price_total" in metrics
        assert "solar_direct_value" in metrics

        # Self-consumption
        assert "self_consumption_ratio" in metrics

        # Weather and temperature
        assert "air_temperature" in metrics
        assert "PaaMH" in metrics

    def test_calculate_metrics_emeters_only(
        self, aggregator, sample_emeters_5min_data, time_window
    ):
        """Test metrics calculation with only emeters data."""
        window_start, window_end = time_window
        raw_data = {
            "emeters": sample_emeters_5min_data,
            "spotprice": None,
            "weather": None,
            "temperatures": None,
        }

        metrics = aggregator.calculate_metrics(raw_data, window_start, window_end)

        assert metrics is not None

        # Energy metrics
        assert "solar_yield_avg" in metrics
        assert "consumption_avg" in metrics

        # Peak power metrics
        assert "consumption_max" in metrics

        # No cost metrics without spotprice
        assert "price_total" not in metrics
        assert "solar_direct_value" not in metrics

        # No weather/temperature data
        assert "air_temperature" not in metrics
        assert "PaaMH" not in metrics

    def test_full_aggregation_pipeline(
        self,
        aggregator,
        sample_emeters_5min_data,
        sample_spotprice,
        sample_weather,
        sample_temperatures,
        time_window,
    ):
        """Test the full aggregation pipeline."""
        window_start, window_end = time_window

        # Mock the fetch methods to return our sample data
        aggregator._fetch_emeters_5min_data = MagicMock(return_value=sample_emeters_5min_data)
        aggregator._fetch_spotprice_data = MagicMock(return_value=sample_spotprice)
        aggregator._fetch_weather_data = MagicMock(return_value=sample_weather)
        aggregator._fetch_temperatures_data = MagicMock(return_value=sample_temperatures)

        # Mock the write to avoid config errors
        aggregator.write_results = MagicMock(return_value=True)

        # Run aggregation
        metrics = aggregator.aggregate_window(window_start, window_end, write_to_influx=True)

        assert metrics is not None
        assert "solar_yield_avg" in metrics
        assert "consumption_avg" in metrics
        assert "consumption_max" in metrics
        assert "self_consumption_ratio" in metrics

        # Verify write was called
        aggregator.write_results.assert_called_once()
