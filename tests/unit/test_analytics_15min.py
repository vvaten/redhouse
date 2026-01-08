"""Unit tests for 15-minute analytics aggregation."""

import datetime
from unittest.mock import MagicMock, patch

import pytest
import pytz

from src.aggregation.analytics_15min import (
    aggregate_15min_window,
    fetch_emeters_5min_data,
)


@pytest.fixture
def sample_emeters_5min_data():
    """Sample emeters_5min data for testing (3 data points = 15 minutes)."""
    base_time = datetime.datetime(2026, 1, 8, 10, 0, 0, tzinfo=pytz.UTC)
    return [
        {
            "time": base_time + datetime.timedelta(minutes=i * 5),
            "solar_yield_avg": 1000.0 + i * 100,  # W
            "solar_yield_diff": 83.33,  # Wh per 5 min
            "consumption_avg": 1500.0,  # W
            "consumption_diff": 125.0,  # Wh per 5 min
            "emeter_avg": 500.0,  # W
            "emeter_diff": 41.67,  # Wh per 5 min
            "battery_charge_avg": 0.0,
            "battery_charge_diff": 0.0,
            "battery_discharge_avg": 0.0,
            "battery_discharge_diff": 0.0,
            "Battery_SoC": 65.0 + i,
            "energy_import_avg": 500.0,  # W
            "energy_export_avg": 0.0,
        }
        for i in range(3)
    ]


@pytest.fixture
def sample_spotprice():
    """Sample spot price data."""
    return {"price_total": 5.5, "price_sell": 3.0}  # c/kWh  # c/kWh


@pytest.fixture
def sample_weather():
    """Sample weather data."""
    return {
        "air_temperature": -2.5,
        "cloud_cover": 75.0,
        "solar_radiation": 50.0,
        "wind_speed": 3.5,
    }


@pytest.fixture
def sample_temperatures():
    """Sample temperature data."""
    return {"PaaMH": 45.0, "Ulkolampo": -2.0, "PalMH": 35.0}


def test_aggregate_15min_window_with_all_sources(
    sample_emeters_5min_data, sample_spotprice, sample_weather, sample_temperatures
):
    """Test aggregation with all data sources."""
    window_end = datetime.datetime(2026, 1, 8, 10, 15, 0, tzinfo=pytz.UTC)

    result = aggregate_15min_window(
        sample_emeters_5min_data,
        sample_spotprice,
        sample_weather,
        sample_temperatures,
        window_end,
    )

    assert result is not None
    assert "solar_yield_avg" in result
    assert "consumption_avg" in result
    assert "solar_yield_sum" in result
    assert "consumption_sum" in result
    assert "price_total" in result
    assert "electricity_cost" in result
    assert "air_temperature" in result
    assert "PaaMH" in result

    # Check averaged power: (1000 + 1100 + 1200) / 3 = 1100 W
    assert result["solar_yield_avg"] == pytest.approx(1100.0, rel=0.01)

    # Check summed energy: 3 x 83.33 Wh = 250 Wh
    assert result["solar_yield_sum"] == pytest.approx(249.99, rel=0.01)

    # Check consumption: 3 x 125 = 375 Wh
    assert result["consumption_sum"] == pytest.approx(375.0, rel=0.01)

    # Check grid import: 3 x 41.67 = 125 Wh
    assert result["emeter_sum"] == pytest.approx(125.01, rel=0.01)

    # Check electricity cost: (125.01 Wh / 1000) * (5.5 c/kWh / 100) = 0.006875 EUR
    assert result["electricity_cost"] == pytest.approx(0.006875, rel=0.01)

    # Check battery SoC is last value
    assert result["Battery_SoC"] == 67.0

    # Check weather data
    assert result["air_temperature"] == -2.5
    assert result["cloud_cover"] == 75.0

    # Check temperature data
    assert result["PaaMH"] == 45.0


def test_aggregate_15min_window_emeters_only(sample_emeters_5min_data):
    """Test aggregation with only emeters data."""
    window_end = datetime.datetime(2026, 1, 8, 10, 15, 0, tzinfo=pytz.UTC)

    result = aggregate_15min_window(sample_emeters_5min_data, None, None, None, window_end)

    assert result is not None
    assert "solar_yield_avg" in result
    assert "consumption_avg" in result
    # Price fields should not exist if no spotprice data
    assert "price_total" not in result
    assert "electricity_cost" not in result
    # Weather fields should not exist
    assert "air_temperature" not in result


def test_aggregate_15min_window_no_data():
    """Test aggregation with no emeters data."""
    window_end = datetime.datetime(2026, 1, 8, 10, 15, 0, tzinfo=pytz.UTC)

    result = aggregate_15min_window([], None, None, None, window_end)

    assert result is None


def test_cost_calculation_with_export(sample_emeters_5min_data, sample_spotprice):
    """Test cost calculation when there is solar export."""
    window_end = datetime.datetime(2026, 1, 8, 10, 15, 0, tzinfo=pytz.UTC)

    # Modify data: high solar, low consumption, to enable export
    for point in sample_emeters_5min_data:
        point["solar_yield_diff"] = 200.0  # 600 Wh total solar
        point["consumption_diff"] = 50.0  # 150 Wh total consumption
        point["battery_charge_diff"] = 0.0  # No battery charging
        point["energy_export_avg"] = 500.0  # W exported (for export_sum calculation)

    result = aggregate_15min_window(
        sample_emeters_5min_data, sample_spotprice, None, None, window_end
    )

    assert result is not None

    # Solar = 600 Wh, Consumption = 150 Wh, Battery = 0 Wh
    # Priority: solar_to_consumption = 150, solar_to_battery = 0, solar_to_export = 450
    assert result["solar_to_consumption"] == pytest.approx(150.0, rel=0.01)
    assert result["solar_to_export"] == pytest.approx(450.0, rel=0.01)

    # Solar export revenue: (450 Wh / 1000) * (3.0 c/kWh / 100) = 0.0135 EUR
    assert result["solar_export_revenue"] == pytest.approx(0.0135, rel=0.01)

    # Solar direct value: (150 Wh / 1000) * (5.5 c/kWh / 100) = 0.00825 EUR
    assert result["solar_direct_value"] == pytest.approx(0.00825, rel=0.01)


def test_self_consumption_ratio_calculation(sample_emeters_5min_data):
    """Test self-consumption ratio calculation."""
    window_end = datetime.datetime(2026, 1, 8, 10, 15, 0, tzinfo=pytz.UTC)

    # Modify data: solar yield = 250 Wh, export = 50 Wh
    for point in sample_emeters_5min_data:
        point["solar_yield_diff"] = 83.33  # 250 Wh total
        point["energy_export_avg"] = 200.0  # 50 Wh total export

    result = aggregate_15min_window(sample_emeters_5min_data, None, None, None, window_end)

    assert result is not None

    # self_consumption_ratio = (250 - 50) / 250 * 100 = 80%
    assert result["self_consumption_ratio"] == pytest.approx(80.0, rel=0.01)


def test_self_consumption_ratio_no_solar():
    """Test self-consumption ratio when there's no solar production."""
    window_end = datetime.datetime(2026, 1, 8, 10, 15, 0, tzinfo=pytz.UTC)

    emeters_data = [
        {
            "time": window_end - datetime.timedelta(minutes=15 - i * 5),
            "solar_yield_avg": 0.0,
            "solar_yield_diff": 0.0,
            "consumption_avg": 1500.0,
            "consumption_diff": 125.0,
            "emeter_avg": 1500.0,
            "emeter_diff": 125.0,
            "battery_charge_avg": 0.0,
            "battery_charge_diff": 0.0,
            "battery_discharge_avg": 0.0,
            "battery_discharge_diff": 0.0,
            "Battery_SoC": 65.0,
            "energy_import_avg": 1500.0,
            "energy_export_avg": 0.0,
        }
        for i in range(3)
    ]

    result = aggregate_15min_window(emeters_data, None, None, None, window_end)

    assert result is not None
    # No solar production, so self-consumption ratio should be 0
    assert result["self_consumption_ratio"] == 0.0


def test_battery_aggregation(sample_emeters_5min_data):
    """Test battery charge/discharge aggregation."""
    window_end = datetime.datetime(2026, 1, 8, 10, 15, 0, tzinfo=pytz.UTC)

    # Add battery activity
    for point in sample_emeters_5min_data:
        point["battery_charge_avg"] = 2000.0  # W
        point["battery_charge_diff"] = 166.67  # Wh per 5 min
        point["battery_discharge_avg"] = 0.0
        point["battery_discharge_diff"] = 0.0

    result = aggregate_15min_window(sample_emeters_5min_data, None, None, None, window_end)

    assert result is not None

    # Average battery charge power: 2000 W
    assert result["battery_charge_avg"] == pytest.approx(2000.0, rel=0.01)

    # Total battery charge energy: 3 x 166.67 = 500 Wh
    assert result["battery_charge_sum"] == pytest.approx(500.01, rel=0.01)


def test_handles_none_values():
    """Test aggregation handles None values gracefully."""
    window_end = datetime.datetime(2026, 1, 8, 10, 15, 0, tzinfo=pytz.UTC)

    emeters_data = [
        {
            "time": window_end - datetime.timedelta(minutes=15 - i * 5),
            "solar_yield_avg": None,
            "solar_yield_diff": None,
            "consumption_avg": 1500.0,
            "consumption_diff": 125.0,
            "emeter_avg": 500.0,
            "emeter_diff": 41.67,
            "battery_charge_avg": None,
            "battery_charge_diff": None,
            "battery_discharge_avg": None,
            "battery_discharge_diff": None,
            "Battery_SoC": 65.0,
            "energy_import_avg": 500.0,
            "energy_export_avg": None,
        }
        for i in range(3)
    ]

    result = aggregate_15min_window(emeters_data, None, None, None, window_end)

    assert result is not None
    # None values should be treated as 0
    assert result["solar_yield_avg"] == 0.0
    assert result["solar_yield_sum"] == 0.0
    assert result["battery_charge_avg"] == 0.0


def test_timestamp_field():
    """Test that timestamp is correctly set."""
    window_end = datetime.datetime(2026, 1, 8, 10, 15, 0, tzinfo=pytz.UTC)

    emeters_data = [
        {
            "time": window_end - datetime.timedelta(minutes=15 - i * 5),
            "solar_yield_avg": 1000.0,
            "solar_yield_diff": 83.33,
            "consumption_avg": 1500.0,
            "consumption_diff": 125.0,
            "emeter_avg": 500.0,
            "emeter_diff": 41.67,
            "battery_charge_avg": 0.0,
            "battery_charge_diff": 0.0,
            "battery_discharge_avg": 0.0,
            "battery_discharge_diff": 0.0,
            "Battery_SoC": 65.0,
            "energy_import_avg": 500.0,
            "energy_export_avg": 0.0,
        }
        for i in range(3)
    ]

    result = aggregate_15min_window(emeters_data, None, None, None, window_end)

    assert result is not None
    assert result["time"] == window_end


@patch("src.aggregation.analytics_15min.InfluxClient")
@patch("src.aggregation.analytics_15min.get_config")
def test_fetch_emeters_5min_data_empty_result(mock_config, mock_client):
    """Test fetching emeters_5min data returns empty list on no data."""
    mock_client_instance = MagicMock()
    mock_client.return_value = mock_client_instance
    mock_client_instance.query_api.query.return_value = []

    mock_config_instance = MagicMock()
    mock_config.return_value = mock_config_instance
    mock_config_instance.influxdb_bucket_emeters_5min = "emeters_5min"
    mock_config_instance.influxdb_org = "test_org"

    start_time = datetime.datetime(2026, 1, 8, 10, 0, 0, tzinfo=pytz.UTC)
    end_time = datetime.datetime(2026, 1, 8, 10, 15, 0, tzinfo=pytz.UTC)

    result = fetch_emeters_5min_data(mock_client_instance, start_time, end_time)

    assert result == []
