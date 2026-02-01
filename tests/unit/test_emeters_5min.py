"""Unit tests for 5-minute energy meter aggregation."""

import datetime
from unittest.mock import MagicMock

import pytest
import pytz

from src.aggregation.emeters_5min import Emeters5MinAggregator
from src.aggregation.emeters_5min_legacy import (
    aggregate_5min_window,
)
from src.common.config import get_config
from src.common.influx_client import InfluxClient


@pytest.fixture
def sample_checkwatt_data():
    """Sample CheckWatt data for testing.

    IMPORTANT: CheckWatt "delta" grouping returns AVERAGE POWER in Watts, not energy in Wh!
    """
    base_time = datetime.datetime(2026, 1, 8, 10, 0, 0, tzinfo=pytz.UTC)
    return [
        {
            "time": base_time + datetime.timedelta(minutes=i),
            "battery_charge": 0.0,
            "battery_discharge": 1380.0,  # W (average power)
            "battery_soc": 68.0 - i,
            "energy_import": 0.0,
            "energy_export": 0.0,
            "solar_yield": 0.0,
        }
        for i in range(5)
    ]


@pytest.fixture
def sample_shelly_data():
    """Sample Shelly EM3 data for testing."""
    base_time = datetime.datetime(2026, 1, 8, 10, 0, 0, tzinfo=pytz.UTC)
    base_energy = 32854000.0
    return [
        {
            "time": base_time + datetime.timedelta(minutes=i),
            "total_power": 1400.0 + i * 10,
            "net_total_energy": base_energy + i * 23.0,  # Cumulative Wh
            "total_energy": base_energy + i * 25.0,
            "total_energy_returned": 8138700.0 + i * 2.0,
            "phase1_voltage": 234.5,
            "phase2_voltage": 234.0,
            "phase3_voltage": 236.0,
            "phase1_current": 2.3,
            "phase2_current": 1.5,
            "phase3_current": 1.7,
            "phase1_pf": 0.67,
            "phase2_pf": 0.04,
            "phase3_pf": -0.89,
        }
        for i in range(5)
    ]


def test_aggregate_5min_window_with_both_sources(sample_checkwatt_data, sample_shelly_data):
    """Test aggregation with both CheckWatt and Shelly data."""
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window(sample_checkwatt_data, sample_shelly_data, window_end)

    assert result is not None
    assert "solar_yield_avg" in result
    assert "battery_discharge_avg" in result
    assert "Battery_SoC" in result
    assert "emeter_avg" in result
    assert "grid_voltage_avg" in result
    assert "consumption_avg" in result

    # Check battery discharge: 1380 W average power
    assert result["battery_discharge_avg"] == pytest.approx(1380.0, rel=0.01)

    # Check battery SoC is last value
    assert result["Battery_SoC"] == 64.0

    # Check grid voltage average
    expected_voltage = (234.5 + 234.0 + 236.0) / 3.0
    assert result["grid_voltage_avg"] == pytest.approx(expected_voltage, rel=0.01)


def test_aggregate_5min_window_checkwatt_only(sample_checkwatt_data):
    """Test aggregation with only CheckWatt data."""
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window(sample_checkwatt_data, [], window_end)

    assert result is not None
    assert "battery_discharge_avg" in result
    assert "Battery_SoC" in result
    assert "emeter_avg" not in result
    assert "consumption_avg" not in result  # Need both sources for consumption


def test_aggregate_5min_window_shelly_only(sample_shelly_data):
    """Test aggregation with only Shelly EM3 data."""
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window([], sample_shelly_data, window_end)

    assert result is not None
    assert "emeter_avg" in result
    assert "grid_voltage_avg" in result
    assert "energy_returned_avg" in result
    assert "battery_discharge_avg" not in result


def test_aggregate_5min_window_no_data():
    """Test aggregation with no data."""
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window([], [], window_end)

    assert result is None


def test_aggregate_5min_window_handles_none_values():
    """Test that None values in data are handled correctly."""
    checkwatt_data = [
        {
            "time": datetime.datetime(2026, 1, 8, 10, 0, 0, tzinfo=pytz.UTC),
            "battery_charge": None,
            "battery_discharge": 1380.0,
            "battery_soc": 68.0,
            "energy_import": None,
            "energy_export": 0.0,
            "solar_yield": None,
        }
    ]
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window(checkwatt_data, [], window_end)

    assert result is not None
    assert "battery_discharge_avg" in result
    # None values should be treated as 0
    assert result["solar_yield_avg"] == 0.0
    assert result["energy_import_avg"] == 0.0


def test_emeter_energy_calculation(sample_shelly_data):
    """Test that emeter energy is calculated correctly from cumulative totals."""
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window([], sample_shelly_data, window_end)

    # Energy difference: (32854092 Wh at t=4) - (32854000 Wh at t=0) = 92 Wh
    # Time difference: 4 minutes = 240 seconds
    # Power: 92 Wh * 3600 / 240 s = 1380 W
    assert "emeter_diff" in result
    assert result["emeter_diff"] > 0


def test_returned_energy_calculation(sample_shelly_data):
    """Test that returned (exported) energy is calculated correctly."""
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window([], sample_shelly_data, window_end)

    assert "energy_returned_avg" in result
    assert "energy_returned_diff" in result
    assert result["energy_returned_diff"] > 0


def test_consumption_calculation():
    """Test consumption calculation formula."""
    checkwatt_data = [
        {
            "time": datetime.datetime(2026, 1, 8, 10, i, 0, tzinfo=pytz.UTC),
            "battery_charge": 100.0,
            "battery_discharge": 200.0,
            "battery_soc": 50.0,
            "energy_import": 50.0,
            "energy_export": 10.0,
            "solar_yield": 150.0,
        }
        for i in range(5)
    ]

    shelly_data = [
        {
            "time": datetime.datetime(2026, 1, 8, 10, i, 0, tzinfo=pytz.UTC),
            "total_power": 500.0,
            "net_total_energy": 10000.0 + i * 8.0,
            "total_energy": 10000.0 + i * 10.0,
            "total_energy_returned": 1000.0 + i * 2.0,
            "phase1_voltage": 230.0,
            "phase2_voltage": 230.0,
            "phase3_voltage": 230.0,
            "phase1_current": 1.0,
            "phase2_current": 1.0,
            "phase3_current": 1.0,
            "phase1_pf": 1.0,
            "phase2_pf": 1.0,
            "phase3_pf": 1.0,
        }
        for i in range(5)
    ]

    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)
    result = aggregate_5min_window(checkwatt_data, shelly_data, window_end)

    # Consumption = grid + solar + battery_discharge - battery_charge
    assert "consumption_avg" in result
    expected = (
        result["emeter_avg"]
        + result["solar_yield_avg"]
        + result["battery_discharge_avg"]
        - result["battery_charge_avg"]
    )
    assert result["consumption_avg"] == pytest.approx(expected, rel=0.01)


def test_grid_metrics_averaging(sample_shelly_data):
    """Test that grid metrics are averaged correctly."""
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window([], sample_shelly_data, window_end)

    # Voltage average: (234.5 + 234.0 + 236.0) / 3 = 234.833...
    assert result["grid_voltage_avg"] == pytest.approx(234.833, rel=0.01)

    # Current average: (2.3 + 1.5 + 1.7) / 3 = 1.833...
    assert result["grid_current_avg"] == pytest.approx(1.833, rel=0.01)

    # Power factor average: (0.67 + 0.04 + (-0.89)) / 3 = -0.06
    assert result["grid_power_factor_avg"] == pytest.approx(-0.06, rel=0.01)


def test_single_shelly_datapoint():
    """Test handling of single Shelly data point."""
    shelly_data = [
        {
            "time": datetime.datetime(2026, 1, 8, 10, 0, 0, tzinfo=pytz.UTC),
            "total_power": 1500.0,
            "net_total_energy": 10000.0,
            "total_energy": 10000.0,
            "total_energy_returned": 1000.0,
            "phase1_voltage": 230.0,
            "phase2_voltage": 230.0,
            "phase3_voltage": 230.0,
            "phase1_current": 1.0,
            "phase2_current": 1.0,
            "phase3_current": 1.0,
            "phase1_pf": 1.0,
            "phase2_pf": 1.0,
            "phase3_pf": 1.0,
        }
    ]
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window([], shelly_data, window_end)

    # With single point, cannot calculate energy difference - should fail
    assert result is None


def test_timestamp_field():
    """Test that timestamp difference is calculated."""
    shelly_data = [
        {
            "time": datetime.datetime(2026, 1, 8, 10, 0, 0, tzinfo=pytz.UTC),
            "total_power": 1000.0,
            "net_total_energy": 10000.0,
            "total_energy": 10000.0,
            "total_energy_returned": 1000.0,
            "phase1_voltage": 230.0,
            "phase2_voltage": 230.0,
            "phase3_voltage": 230.0,
            "phase1_current": 1.0,
            "phase2_current": 1.0,
            "phase3_current": 1.0,
            "phase1_pf": 1.0,
            "phase2_pf": 1.0,
            "phase3_pf": 1.0,
        },
        {
            "time": datetime.datetime(2026, 1, 8, 10, 3, 0, tzinfo=pytz.UTC),
            "total_power": 1000.0,
            "net_total_energy": 10050.0,
            "total_energy": 10050.0,
            "total_energy_returned": 1000.0,
            "phase1_voltage": 230.0,
            "phase2_voltage": 230.0,
            "phase3_voltage": 230.0,
            "phase1_current": 1.0,
            "phase2_current": 1.0,
            "phase3_current": 1.0,
            "phase1_pf": 1.0,
            "phase2_pf": 1.0,
            "phase3_pf": 1.0,
        },
    ]
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window([], shelly_data, window_end)

    # Time difference should be 3 minutes = 180 seconds
    assert result["ts_diff"] == 180.0


def test_counter_reset_detection():
    """Test that counter resets are detected and handled with averaged power gap-fill."""
    # Simulate counter reset where end < start (large decrease indicates reset)
    shelly_data = [
        {
            "time": datetime.datetime(2026, 1, 8, 10, 0, 0, tzinfo=pytz.UTC),
            "total_power": 1000.0,
            "net_total_energy": 50000.0,  # Large value before reset
            "total_energy": 50000.0,
            "total_energy_returned": 10000.0,
            "phase1_voltage": 230.0,
            "phase2_voltage": 230.0,
            "phase3_voltage": 230.0,
            "phase1_current": 1.0,
            "phase2_current": 1.0,
            "phase3_current": 1.0,
            "phase1_pf": 1.0,
            "phase2_pf": 1.0,
            "phase3_pf": 1.0,
        },
        {
            "time": datetime.datetime(2026, 1, 8, 10, 3, 0, tzinfo=pytz.UTC),
            "total_power": 1200.0,
            "net_total_energy": 100.0,  # Counter reset to small value
            "total_energy": 100.0,
            "total_energy_returned": 101.0,  # Must be > 100 to pass sanity check
            "phase1_voltage": 230.0,
            "phase2_voltage": 230.0,
            "phase3_voltage": 230.0,
            "phase1_current": 1.0,
            "phase2_current": 1.0,
            "phase3_current": 1.0,
            "phase1_pf": 1.0,
            "phase2_pf": 1.0,
            "phase3_pf": 1.0,
        },
    ]
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window([], shelly_data, window_end)

    # Should use averaged power for gap-fill: avg(1000, 1200) = 1100W
    # Time diff: 180 seconds = 0.05 hours
    # Energy: 1100W * 0.05h = 55 Wh
    assert result is not None, "Should succeed with gap-fill"
    assert abs(result["emeter_diff"] - 55.0) < 1.0, f"Expected ~55 Wh, got {result['emeter_diff']}"
    assert (
        abs(result["emeter_avg"] - 1100.0) < 10.0
    ), f"Expected ~1100 W, got {result['emeter_avg']}"


def test_small_start_value_detection():
    """Test that small start values (missing data) are detected."""
    shelly_data = [
        {
            "time": datetime.datetime(2026, 1, 8, 10, 0, 0, tzinfo=pytz.UTC),
            "total_power": 1000.0,
            "net_total_energy": 50.0,  # Suspiciously small start value
            "total_energy": 50.0,
            "total_energy_returned": 10.0,
            "phase1_voltage": 230.0,
            "phase2_voltage": 230.0,
            "phase3_voltage": 230.0,
            "phase1_current": 1.0,
            "phase2_current": 1.0,
            "phase3_current": 1.0,
            "phase1_pf": 1.0,
            "phase2_pf": 1.0,
            "phase3_pf": 1.0,
        },
        {
            "time": datetime.datetime(2026, 1, 8, 10, 3, 0, tzinfo=pytz.UTC),
            "total_power": 1200.0,
            "net_total_energy": 100.0,
            "total_energy": 100.0,
            "total_energy_returned": 20.0,
            "phase1_voltage": 230.0,
            "phase2_voltage": 230.0,
            "phase3_voltage": 230.0,
            "phase1_current": 1.0,
            "phase2_current": 1.0,
            "phase3_current": 1.0,
            "phase1_pf": 1.0,
            "phase2_pf": 1.0,
            "phase3_pf": 1.0,
        },
    ]
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window([], shelly_data, window_end)

    # Should fail due to insufficient data (counters < 100 Wh)
    assert result is None


def test_unreasonable_energy_diff():
    """Test that large energy diffs (without counter reset) are calculated correctly."""
    shelly_data = [
        {
            "time": datetime.datetime(2026, 1, 8, 10, 0, 0, tzinfo=pytz.UTC),
            "total_power": 1000.0,
            "net_total_energy": 10000.0,
            "total_energy": 10000.0,
            "total_energy_returned": 1000.0,
            "phase1_voltage": 230.0,
            "phase2_voltage": 230.0,
            "phase3_voltage": 230.0,
            "phase1_current": 1.0,
            "phase2_current": 1.0,
            "phase3_current": 1.0,
            "phase1_pf": 1.0,
            "phase2_pf": 1.0,
            "phase3_pf": 1.0,
        },
        {
            "time": datetime.datetime(2026, 1, 8, 10, 3, 0, tzinfo=pytz.UTC),
            "total_power": 1200.0,
            "net_total_energy": 20000.0,  # 10000 Wh in 3 minutes = unreasonable
            "total_energy": 20000.0,
            "total_energy_returned": 1050.0,
            "phase1_voltage": 230.0,
            "phase2_voltage": 230.0,
            "phase3_voltage": 230.0,
            "phase1_current": 1.0,
            "phase2_current": 1.0,
            "phase3_current": 1.0,
            "phase1_pf": 1.0,
            "phase2_pf": 1.0,
            "phase3_pf": 1.0,
        },
    ]
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window([], shelly_data, window_end)

    # No counter reset (counters increasing), just a large energy consumption
    # Should calculate actual diff: 20000 - 10000 = 10000 Wh
    assert result is not None, "Should succeed with normal calculation"
    assert abs(result["emeter_diff"] - 10000.0) < 1.0
    # avg_power = 10000 Wh / 0.05 h = 200000 W
    assert abs(result["emeter_avg"] - 200000.0) < 100.0


def test_reasonable_energy_calculation():
    """Test that reasonable energy values are calculated correctly."""
    shelly_data = [
        {
            "time": datetime.datetime(2026, 1, 8, 10, 0, 0, tzinfo=pytz.UTC),
            "total_power": 1000.0,
            "net_total_energy": 10000.0,
            "total_energy": 10000.0,
            "total_energy_returned": 1000.0,
            "phase1_voltage": 230.0,
            "phase2_voltage": 230.0,
            "phase3_voltage": 230.0,
            "phase1_current": 1.0,
            "phase2_current": 1.0,
            "phase3_current": 1.0,
            "phase1_pf": 1.0,
            "phase2_pf": 1.0,
            "phase3_pf": 1.0,
        },
        {
            "time": datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC),
            "total_power": 1200.0,
            "net_total_energy": 10100.0,  # 100 Wh in 5 minutes = reasonable
            "total_energy": 10100.0,
            "total_energy_returned": 1010.0,  # 10 Wh returned
            "phase1_voltage": 230.0,
            "phase2_voltage": 230.0,
            "phase3_voltage": 230.0,
            "phase1_current": 1.0,
            "phase2_current": 1.0,
            "phase3_current": 1.0,
            "phase1_pf": 1.0,
            "phase2_pf": 1.0,
            "phase3_pf": 1.0,
        },
    ]
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window([], shelly_data, window_end)

    # Should calculate diff correctly
    assert result["emeter_diff"] == 100.0
    # Power = 100 Wh * 3600 / 300 s = 1200 W
    assert result["emeter_avg"] == pytest.approx(1200.0, rel=0.01)
    # Returned energy
    assert result["energy_returned_diff"] == 10.0
    assert result["energy_returned_avg"] == pytest.approx(120.0, rel=0.01)


def test_checkwatt_unreasonable_values():
    """Test that unreasonable CheckWatt values are detected and zeroed."""
    checkwatt_data = [
        {
            "time": datetime.datetime(2026, 1, 8, 10, i, 0, tzinfo=pytz.UTC),
            "battery_charge": 0.0,
            "battery_discharge": 0.0,
            "battery_soc": 50.0,
            "energy_import": 50000.0,  # Unreasonable: 50 kWh in 1 minute
            "energy_export": 0.0,
            "solar_yield": 0.0,
        }
        for i in range(5)
    ]
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window(checkwatt_data, [], window_end)

    # Unreasonable import value should be zeroed
    assert result["energy_import_avg"] == 0.0
    assert result["cw_emeter_avg"] == 0.0


def test_checkwatt_reasonable_battery_discharge():
    """Test that reasonable CheckWatt battery discharge is calculated correctly."""
    checkwatt_data = [
        {
            "time": datetime.datetime(2026, 1, 8, 10, i, 0, tzinfo=pytz.UTC),
            "battery_charge": 0.0,
            "battery_discharge": 100.0,  # 100 W (average power)
            "battery_soc": 50.0 - i,
            "energy_import": 0.0,
            "energy_export": 0.0,
            "solar_yield": 0.0,
        }
        for i in range(5)
    ]
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window(checkwatt_data, [], window_end)

    # Average power = 100 W
    assert result["battery_discharge_avg"] == pytest.approx(100.0, rel=0.01)
    # Energy over 5 minutes = 100 W * 5/60 hours = 8.33 Wh
    assert result["battery_discharge_diff"] == pytest.approx(8.333, rel=0.01)


def test_checkwatt_unreasonable_battery_discharge():
    """Test that unreasonable CheckWatt battery discharge is detected."""
    checkwatt_data = [
        {
            "time": datetime.datetime(2026, 1, 8, 10, i, 0, tzinfo=pytz.UTC),
            "battery_charge": 0.0,
            "battery_discharge": 30000.0,  # Unreasonable: 30 kW (over max 25 kW)
            "battery_soc": 50.0,
            "energy_import": 0.0,
            "energy_export": 0.0,
            "solar_yield": 0.0,
        }
        for i in range(5)
    ]
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window(checkwatt_data, [], window_end)

    # Unreasonable discharge should be zeroed
    assert result["battery_discharge_diff"] == 0.0
    assert result["battery_discharge_avg"] == 0.0


def test_checkwatt_unreasonable_solar():
    """Test that unreasonable CheckWatt solar values are detected."""
    checkwatt_data = [
        {
            "time": datetime.datetime(2026, 1, 8, 10, i, 0, tzinfo=pytz.UTC),
            "battery_charge": 0.0,
            "battery_discharge": 0.0,
            "battery_soc": 50.0,
            "energy_import": 0.0,
            "energy_export": 0.0,
            "solar_yield": 30000.0,  # Unreasonable: 30 kW (over max 25 kW)
        }
        for i in range(5)
    ]
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window(checkwatt_data, [], window_end)

    # Unreasonable solar should be zeroed
    assert result["solar_yield_diff"] == 0.0
    assert result["solar_yield_avg"] == 0.0


def test_checkwatt_multiple_suspicious_values():
    """Test handling of multiple suspicious CheckWatt values at once."""
    checkwatt_data = [
        {
            "time": datetime.datetime(2026, 1, 8, 10, i, 0, tzinfo=pytz.UTC),
            "battery_charge": 5000.0,  # 5 kW - reasonable for home battery
            "battery_discharge": 6000.0,  # 6 kW - reasonable
            "battery_soc": 50.0,
            "energy_import": 40000.0,  # 40 kW - suspicious (over 25 kW limit)
            "energy_export": 30000.0,  # 30 kW - suspicious (over 25 kW limit)
            "solar_yield": 26000.0,  # 26 kW - suspicious (over 25 kW limit)
        }
        for i in range(5)
    ]
    window_end = datetime.datetime(2026, 1, 8, 10, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window(checkwatt_data, [], window_end)

    # Suspicious values (over 25 kW) should be zeroed
    assert result["solar_yield_diff"] == 0.0
    assert result["energy_import_avg"] == 0.0
    assert result["energy_export_avg"] == 0.0

    # Reasonable values should be preserved
    # 5000 W * 5/60 hours = 416.67 Wh
    assert result["battery_charge_diff"] == pytest.approx(416.667, rel=0.01)
    # 6000 W * 5/60 hours = 500 Wh
    assert result["battery_discharge_diff"] == pytest.approx(500.0, rel=0.01)


def test_checkwatt_reasonable_solar_production():
    """Test reasonable solar production values."""
    checkwatt_data = [
        {
            "time": datetime.datetime(2026, 1, 8, 12, i, 0, tzinfo=pytz.UTC),
            "battery_charge": 50.0,  # 50 W
            "battery_discharge": 0.0,
            "battery_soc": 60.0 + i,
            "energy_import": 0.0,
            "energy_export": 100.0,  # 100 W exported
            "solar_yield": 200.0,  # 200 W = reasonable for midday
        }
        for i in range(5)
    ]
    window_end = datetime.datetime(2026, 1, 8, 12, 5, 0, tzinfo=pytz.UTC)

    result = aggregate_5min_window(checkwatt_data, [], window_end)

    # All values should be calculated (reasonable)
    # Average power values
    assert result["solar_yield_avg"] == pytest.approx(200.0, rel=0.01)
    assert result["battery_charge_avg"] == pytest.approx(50.0, rel=0.01)
    assert result["energy_export_avg"] == pytest.approx(100.0, rel=0.01)
    # Energy over 5 minutes (W * 5/60 hours = Wh)
    assert result["solar_yield_diff"] == pytest.approx(16.667, rel=0.01)  # 200 * 5/60
    assert result["battery_charge_diff"] == pytest.approx(4.167, rel=0.01)  # 50 * 5/60


# =============================================================================
# Tests for Emeters5MinAggregator (refactored class-based implementation)
# =============================================================================


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
    """Create an Emeters5MinAggregator instance."""
    return Emeters5MinAggregator(mock_influx_client, config)


@pytest.fixture
def time_window():
    """Create a test time window."""
    tz = pytz.timezone("Europe/Helsinki")
    window_start = tz.localize(datetime.datetime(2026, 1, 8, 10, 0, 0))
    window_end = tz.localize(datetime.datetime(2026, 1, 8, 10, 5, 0))
    return window_start, window_end


class TestEmeters5MinAggregator:
    """Test the refactored Emeters5MinAggregator class."""

    def test_initialization(self, aggregator, mock_influx_client, config):
        """Test that aggregator is initialized correctly."""
        assert aggregator.influx == mock_influx_client
        assert aggregator.config == config
        assert aggregator.INTERVAL_SECONDS == 300
        assert aggregator.MAX_REASONABLE_POWER == 25000.0

    def test_validate_data_with_both_sources(
        self, aggregator, sample_checkwatt_data, sample_shelly_data
    ):
        """Test validation with both CheckWatt and Shelly data."""
        raw_data = {"checkwatt": sample_checkwatt_data, "shelly": sample_shelly_data}
        assert aggregator.validate_data(raw_data) is True

    def test_validate_data_checkwatt_only(self, aggregator, sample_checkwatt_data):
        """Test validation with only CheckWatt data."""
        raw_data = {"checkwatt": sample_checkwatt_data, "shelly": []}
        assert aggregator.validate_data(raw_data) is True

    def test_validate_data_no_data(self, aggregator):
        """Test validation with no data."""
        raw_data = {"checkwatt": [], "shelly": []}
        assert aggregator.validate_data(raw_data) is False

    def test_calculate_checkwatt_metrics(self, aggregator, sample_checkwatt_data):
        """Test CheckWatt metrics calculation."""
        metrics = aggregator._calculate_checkwatt_metrics(sample_checkwatt_data)

        assert "solar_yield_avg" in metrics
        assert "battery_discharge_avg" in metrics
        assert "Battery_SoC" in metrics

        # Check battery discharge: 1380 W average
        assert metrics["battery_discharge_avg"] == pytest.approx(1380.0, rel=0.01)

        # Check battery SoC is last value
        assert metrics["Battery_SoC"] == 64.0

    def test_calculate_shelly_metrics(self, aggregator, sample_shelly_data):
        """Test Shelly EM3 metrics calculation."""
        metrics = aggregator._calculate_shelly_metrics(sample_shelly_data)

        assert metrics is not None
        assert "emeter_avg" in metrics
        assert "grid_voltage_avg" in metrics

    def test_full_aggregation_pipeline(
        self, aggregator, sample_checkwatt_data, sample_shelly_data, time_window, config
    ):
        """Test the full aggregation pipeline."""
        window_start, window_end = time_window

        # Mock the fetch methods to return our sample data
        aggregator._fetch_checkwatt_data = MagicMock(return_value=sample_checkwatt_data)
        aggregator._fetch_shelly_data = MagicMock(return_value=sample_shelly_data)

        # Mock the write to avoid config errors
        aggregator.write_results = MagicMock(return_value=True)

        # Run aggregation
        metrics = aggregator.aggregate_window(window_start, window_end, write_to_influx=True)

        assert metrics is not None
        assert "solar_yield_avg" in metrics
        assert "consumption_avg" in metrics

        # Verify write was called
        aggregator.write_results.assert_called_once()
