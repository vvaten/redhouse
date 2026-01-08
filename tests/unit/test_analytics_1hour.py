"""Unit tests for 1-hour analytics aggregation."""

import datetime
from unittest.mock import MagicMock, patch

import pytest
import pytz

from src.aggregation.analytics_1hour import (
    aggregate_1hour_window,
    fetch_emeters_5min_data,
)


@pytest.fixture
def sample_emeters_5min_data():
    """Sample emeters_5min data for testing (12 data points = 1 hour)."""
    base_time = datetime.datetime(2026, 1, 8, 10, 0, 0, tzinfo=pytz.UTC)
    return [
        {
            "time": base_time + datetime.timedelta(minutes=i * 5),
            "solar_yield_avg": 2000.0 + i * 50,  # W
            "solar_yield_diff": 166.67,  # Wh per 5 min
            "consumption_avg": 3000.0,  # W
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
    return {"price_total": 8.5, "price_sell": 4.0}  # c/kWh  # c/kWh


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


def test_aggregate_1hour_window_with_all_sources(
    sample_emeters_5min_data, sample_spotprice, sample_weather, sample_temperatures
):
    """Test aggregation with all data sources."""
    window_end = datetime.datetime(2026, 1, 8, 11, 0, 0, tzinfo=pytz.UTC)

    result = aggregate_1hour_window(
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
    assert "consumption_max" in result
    assert "solar_yield_max" in result
    assert "grid_power_max" in result
    assert "price_total" in result
    assert "electricity_cost" in result
    assert "air_temperature" in result
    assert "PaaMH" in result

    # Check averaged power: (2000 + 2050 + ... + 2550) / 12 = 2275 W
    assert result["solar_yield_avg"] == pytest.approx(2275.0, rel=0.01)

    # Check summed energy: 12 x 166.67 Wh = 2000 Wh
    assert result["solar_yield_sum"] == pytest.approx(2000.04, rel=0.01)

    # Check consumption: 12 x 250 = 3000 Wh
    assert result["consumption_sum"] == pytest.approx(3000.0, rel=0.01)

    # Check grid import: 12 x 83.33 = 999.96 Wh
    assert result["emeter_sum"] == pytest.approx(999.96, rel=0.01)

    # Check peak consumption: 3000 W (constant in test data)
    assert result["consumption_max"] == 3000.0

    # Check peak solar: 2550 W (last point has highest value)
    assert result["solar_yield_max"] == 2550.0

    # Check electricity cost: (999.96 Wh / 1000) * (8.5 c/kWh / 100) = 0.0849966 EUR
    assert result["electricity_cost"] == pytest.approx(0.0849966, rel=0.01)

    # Check battery SoC is last value
    assert result["Battery_SoC"] == 76.0

    # Check weather data
    assert result["air_temperature"] == 5.5
    assert result["cloud_cover"] == 50.0

    # Check temperature data
    assert result["PaaMH"] == 50.0


def test_aggregate_1hour_window_emeters_only(sample_emeters_5min_data):
    """Test aggregation with only emeters data."""
    window_end = datetime.datetime(2026, 1, 8, 11, 0, 0, tzinfo=pytz.UTC)

    result = aggregate_1hour_window(sample_emeters_5min_data, None, None, None, window_end)

    assert result is not None
    assert "solar_yield_avg" in result
    assert "consumption_avg" in result
    assert "consumption_max" in result
    # Price fields should not exist if no spotprice data
    assert "price_total" not in result
    assert "electricity_cost" not in result
    # Weather fields should not exist
    assert "air_temperature" not in result


def test_aggregate_1hour_window_no_data():
    """Test aggregation with no emeters data."""
    window_end = datetime.datetime(2026, 1, 8, 11, 0, 0, tzinfo=pytz.UTC)

    result = aggregate_1hour_window([], None, None, None, window_end)

    assert result is None


def test_cost_calculation_with_export(sample_emeters_5min_data, sample_spotprice):
    """Test cost calculation when there is solar export."""
    window_end = datetime.datetime(2026, 1, 8, 11, 0, 0, tzinfo=pytz.UTC)

    # Modify data: high solar, low consumption, to enable export
    for point in sample_emeters_5min_data:
        point["solar_yield_diff"] = 300.0  # 3600 Wh total solar (12 points)
        point["consumption_diff"] = 100.0  # 1200 Wh total consumption
        point["battery_charge_diff"] = 0.0  # No battery charging
        point["energy_export_avg"] = 2000.0  # W exported (for export_sum calculation)

    result = aggregate_1hour_window(
        sample_emeters_5min_data, sample_spotprice, None, None, window_end
    )

    assert result is not None

    # Solar = 3600 Wh, Consumption = 1200 Wh, Battery = 0 Wh
    # Priority: solar_to_consumption = 1200, solar_to_battery = 0, solar_to_export = 2400
    assert result["solar_to_consumption"] == pytest.approx(1200.0, rel=0.01)
    assert result["solar_to_export"] == pytest.approx(2400.0, rel=0.01)

    # Solar export revenue: (2400 Wh / 1000) * (4.0 c/kWh / 100) = 0.096 EUR
    assert result["solar_export_revenue"] == pytest.approx(0.096, rel=0.01)

    # Solar direct value: (1200 Wh / 1000) * (8.5 c/kWh / 100) = 0.102 EUR
    assert result["solar_direct_value"] == pytest.approx(0.102, rel=0.01)


def test_self_consumption_ratio_calculation(sample_emeters_5min_data):
    """Test self-consumption ratio calculation."""
    window_end = datetime.datetime(2026, 1, 8, 11, 0, 0, tzinfo=pytz.UTC)

    # Modify data: solar yield = 2000 Wh, export = 500 Wh
    for point in sample_emeters_5min_data:
        point["solar_yield_diff"] = 166.67  # 2000 Wh total
        point["energy_export_avg"] = 500.0  # 500 Wh total export

    result = aggregate_1hour_window(sample_emeters_5min_data, None, None, None, window_end)

    assert result is not None

    # self_consumption_ratio = (2000 - 500) / 2000 * 100 = 75%
    assert result["self_consumption_ratio"] == pytest.approx(75.0, rel=0.01)


def test_self_consumption_ratio_no_solar():
    """Test self-consumption ratio when there's no solar production."""
    window_end = datetime.datetime(2026, 1, 8, 11, 0, 0, tzinfo=pytz.UTC)

    emeters_data = [
        {
            "time": window_end - datetime.timedelta(minutes=60 - i * 5),
            "solar_yield_avg": 0.0,
            "solar_yield_diff": 0.0,
            "consumption_avg": 3000.0,
            "consumption_diff": 250.0,
            "emeter_avg": 3000.0,
            "emeter_diff": 250.0,
            "battery_charge_avg": 0.0,
            "battery_charge_diff": 0.0,
            "battery_discharge_avg": 0.0,
            "battery_discharge_diff": 0.0,
            "Battery_SoC": 65.0,
            "energy_import_avg": 3000.0,
            "energy_export_avg": 0.0,
        }
        for i in range(12)
    ]

    result = aggregate_1hour_window(emeters_data, None, None, None, window_end)

    assert result is not None
    # No solar production, so self-consumption ratio should be 0
    assert result["self_consumption_ratio"] == 0.0


def test_peak_values():
    """Test that peak values are correctly identified."""
    window_end = datetime.datetime(2026, 1, 8, 11, 0, 0, tzinfo=pytz.UTC)

    emeters_data = [
        {
            "time": window_end - datetime.timedelta(minutes=60 - i * 5),
            "solar_yield_avg": 1000.0 + i * 200,  # Increases each interval
            "solar_yield_diff": 83.33,
            "consumption_avg": 2000.0 + i * 100,  # Increases each interval
            "consumption_diff": 166.67,
            "emeter_avg": 500.0 + i * 50,  # Increases each interval
            "emeter_diff": 41.67,
            "battery_charge_avg": 0.0,
            "battery_charge_diff": 0.0,
            "battery_discharge_avg": 0.0,
            "battery_discharge_diff": 0.0,
            "Battery_SoC": 65.0,
            "energy_import_avg": 500.0,
            "energy_export_avg": 0.0,
        }
        for i in range(12)
    ]

    result = aggregate_1hour_window(emeters_data, None, None, None, window_end)

    assert result is not None

    # Peak solar: 1000 + 11*200 = 3200 W
    assert result["solar_yield_max"] == 3200.0

    # Peak consumption: 2000 + 11*100 = 3100 W
    assert result["consumption_max"] == 3100.0

    # Peak grid power: 500 + 11*50 = 1050 W
    assert result["grid_power_max"] == 1050.0


def test_battery_aggregation(sample_emeters_5min_data):
    """Test battery charge/discharge aggregation."""
    window_end = datetime.datetime(2026, 1, 8, 11, 0, 0, tzinfo=pytz.UTC)

    # Add battery activity
    for point in sample_emeters_5min_data:
        point["battery_charge_avg"] = 3000.0  # W
        point["battery_charge_diff"] = 250.0  # Wh per 5 min
        point["battery_discharge_avg"] = 0.0
        point["battery_discharge_diff"] = 0.0

    result = aggregate_1hour_window(sample_emeters_5min_data, None, None, None, window_end)

    assert result is not None

    # Average battery charge power: 3000 W
    assert result["battery_charge_avg"] == pytest.approx(3000.0, rel=0.01)

    # Total battery charge energy: 12 x 250 = 3000 Wh
    assert result["battery_charge_sum"] == pytest.approx(3000.0, rel=0.01)


def test_handles_none_values():
    """Test aggregation handles None values gracefully."""
    window_end = datetime.datetime(2026, 1, 8, 11, 0, 0, tzinfo=pytz.UTC)

    emeters_data = [
        {
            "time": window_end - datetime.timedelta(minutes=60 - i * 5),
            "solar_yield_avg": None,
            "solar_yield_diff": None,
            "consumption_avg": 3000.0,
            "consumption_diff": 250.0,
            "emeter_avg": 1000.0,
            "emeter_diff": 83.33,
            "battery_charge_avg": None,
            "battery_charge_diff": None,
            "battery_discharge_avg": None,
            "battery_discharge_diff": None,
            "Battery_SoC": 65.0,
            "energy_import_avg": 1000.0,
            "energy_export_avg": None,
        }
        for i in range(12)
    ]

    result = aggregate_1hour_window(emeters_data, None, None, None, window_end)

    assert result is not None
    # None values should be treated as 0
    assert result["solar_yield_avg"] == 0.0
    assert result["solar_yield_sum"] == 0.0
    assert result["battery_charge_avg"] == 0.0


def test_timestamp_field():
    """Test that timestamp is correctly set."""
    window_end = datetime.datetime(2026, 1, 8, 11, 0, 0, tzinfo=pytz.UTC)

    emeters_data = [
        {
            "time": window_end - datetime.timedelta(minutes=60 - i * 5),
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
        for i in range(12)
    ]

    result = aggregate_1hour_window(emeters_data, None, None, None, window_end)

    assert result is not None
    assert result["time"] == window_end


@patch("src.aggregation.analytics_1hour.InfluxClient")
@patch("src.aggregation.analytics_1hour.get_config")
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
    end_time = datetime.datetime(2026, 1, 8, 11, 0, 0, tzinfo=pytz.UTC)

    result = fetch_emeters_5min_data(mock_client_instance, start_time, end_time)

    assert result == []
