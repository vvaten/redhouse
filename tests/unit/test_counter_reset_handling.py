"""Unit tests for Shelly EM3 counter reset handling in 5-minute aggregator."""

import datetime

from src.aggregation.emeters_5min import aggregate_5min_window


def test_counter_reset_uses_averaged_power():
    """Test that counter reset detection uses averaged power for gap-fill."""
    # Create 5 data points with counter reset at minute 3
    base_time = datetime.datetime(2026, 1, 8, 12, 0, 0, tzinfo=datetime.timezone.utc)

    checkwatt_data = []
    shelly_data = []

    # Minutes 0-2: Normal operation, importing 1000W
    for i in range(3):
        timestamp = base_time + datetime.timedelta(minutes=i)
        elapsed_hours = i / 60.0

        checkwatt_data.append(
            {
                "time": timestamp,
                "solar_yield": 500.0,
                "battery_charge": 0.0,
                "battery_discharge": 0.0,
                "battery_soc": 50.0,
                "energy_import": 1000.0,
                "energy_export": 0.0,
            }
        )

        # Cumulative counters increase normally
        shelly_data.append(
            {
                "time": timestamp,
                "total_power": 1000.0,
                "total_energy": 1000000.0 + (1000.0 * elapsed_hours),  # Importing
                "total_energy_returned": 500000.0,  # Stays constant
                "net_total_energy": 500000.0 + (1000.0 * elapsed_hours),
                "phase1_voltage": 230.0,
                "phase2_voltage": 230.0,
                "phase3_voltage": 230.0,
                "phase1_current": 1.5,
                "phase2_current": 1.5,
                "phase3_current": 1.5,
                "phase1_pf": 1.0,
                "phase2_pf": 1.0,
                "phase3_pf": 1.0,
            }
        )

    # Minute 3: Counter RESET - both counters jump to near-zero
    # Device rebooted, counters start from ~0
    timestamp = base_time + datetime.timedelta(minutes=3)
    checkwatt_data.append(
        {
            "time": timestamp,
            "solar_yield": 500.0,
            "battery_charge": 0.0,
            "battery_discharge": 0.0,
            "battery_soc": 50.0,
            "energy_import": 800.0,  # Power changed to 800W after reboot
            "energy_export": 0.0,
        }
    )

    # After reset: counters start from low values
    elapsed_hours_after_reset = 0 / 60.0  # Just rebooted
    shelly_data.append(
        {
            "time": timestamp,
            "total_power": 800.0,  # Different power after reboot
            "total_energy": 150.0 + (800.0 * elapsed_hours_after_reset),  # Reset to ~0
            "total_energy_returned": 120.0,  # Reset to ~0
            "net_total_energy": 30.0 + (800.0 * elapsed_hours_after_reset),
            "phase1_voltage": 230.0,
            "phase2_voltage": 230.0,
            "phase3_voltage": 230.0,
            "phase1_current": 1.2,
            "phase2_current": 1.2,
            "phase3_current": 1.2,
            "phase1_pf": 1.0,
            "phase2_pf": 1.0,
            "phase3_pf": 1.0,
        }
    )

    # Minute 4: Continue with new counters
    timestamp = base_time + datetime.timedelta(minutes=4)
    checkwatt_data.append(
        {
            "time": timestamp,
            "solar_yield": 500.0,
            "battery_charge": 0.0,
            "battery_discharge": 0.0,
            "battery_soc": 50.0,
            "energy_import": 800.0,
            "energy_export": 0.0,
        }
    )

    elapsed_hours_after_reset = 1 / 60.0
    shelly_data.append(
        {
            "time": timestamp,
            "total_power": 800.0,
            "total_energy": 150.0 + (800.0 * elapsed_hours_after_reset),
            "total_energy_returned": 120.0,
            "net_total_energy": 30.0 + (800.0 * elapsed_hours_after_reset),
            "phase1_voltage": 230.0,
            "phase2_voltage": 230.0,
            "phase3_voltage": 230.0,
            "phase1_current": 1.2,
            "phase2_current": 1.2,
            "phase3_current": 1.2,
            "phase1_pf": 1.0,
            "phase2_pf": 1.0,
            "phase3_pf": 1.0,
        }
    )

    window_end = base_time + datetime.timedelta(minutes=5)

    # Aggregate the data
    result = aggregate_5min_window(checkwatt_data, shelly_data, window_end)

    # Verify aggregation succeeded
    assert result is not None, "Aggregation should succeed despite counter reset"

    # Calculate expected energy:
    # Minutes 0->1: 1000W * 1/60h = 16.67 Wh
    # Minutes 1->2: 1000W * 1/60h = 16.67 Wh
    # Minutes 2->3 (RESET): avg(1000, 800) = 900W * 1/60h = 15.0 Wh (gap-filled)
    # Minutes 3->4: 800W * 1/60h = 13.33 Wh
    # Total: 16.67 + 16.67 + 15.0 + 13.33 = 61.67 Wh

    expected_energy = 61.67  # Wh
    assert (
        abs(result["emeter_diff"] - expected_energy) < 0.5
    ), f"Expected emeter_diff ~{expected_energy} Wh, got {result['emeter_diff']} Wh"

    # Verify average power
    # Total time: 4 minutes = 240 seconds
    # Average power = (61.67 Wh * 3600) / 240s = 925 W
    expected_avg_power = 925.0
    assert (
        abs(result["emeter_avg"] - expected_avg_power) < 10.0
    ), f"Expected emeter_avg ~{expected_avg_power} W, got {result['emeter_avg']} W"


def test_no_counter_reset_normal_operation():
    """Test normal operation without counter reset."""
    base_time = datetime.datetime(2026, 1, 8, 12, 0, 0, tzinfo=datetime.timezone.utc)

    checkwatt_data = []
    shelly_data = []

    # 5 data points, all normal operation at 1000W
    for i in range(5):
        timestamp = base_time + datetime.timedelta(minutes=i)
        elapsed_hours = i / 60.0

        checkwatt_data.append(
            {
                "time": timestamp,
                "solar_yield": 500.0,
                "battery_charge": 0.0,
                "battery_discharge": 0.0,
                "battery_soc": 50.0,
                "energy_import": 1000.0,
                "energy_export": 0.0,
            }
        )

        shelly_data.append(
            {
                "time": timestamp,
                "total_power": 1000.0,
                "total_energy": 1000000.0 + (1000.0 * elapsed_hours),
                "total_energy_returned": 500000.0,
                "net_total_energy": 500000.0 + (1000.0 * elapsed_hours),
                "phase1_voltage": 230.0,
                "phase2_voltage": 230.0,
                "phase3_voltage": 230.0,
                "phase1_current": 1.5,
                "phase2_current": 1.5,
                "phase3_current": 1.5,
                "phase1_pf": 1.0,
                "phase2_pf": 1.0,
                "phase3_pf": 1.0,
            }
        )

    window_end = base_time + datetime.timedelta(minutes=5)
    result = aggregate_5min_window(checkwatt_data, shelly_data, window_end)

    assert result is not None

    # Expected: 1000W * 4/60h = 66.67 Wh (4 minutes of data)
    expected_energy = 66.67
    assert (
        abs(result["emeter_diff"] - expected_energy) < 0.5
    ), f"Expected emeter_diff ~{expected_energy} Wh, got {result['emeter_diff']} Wh"


def test_export_scenario_with_negative_net_energy():
    """Test export scenario where net_total_energy decreases (normal behavior)."""
    base_time = datetime.datetime(2026, 1, 8, 12, 0, 0, tzinfo=datetime.timezone.utc)

    checkwatt_data = []
    shelly_data = []

    # 5 data points exporting at -800W (solar > consumption)
    for i in range(5):
        timestamp = base_time + datetime.timedelta(minutes=i)
        elapsed_hours = i / 60.0

        checkwatt_data.append(
            {
                "time": timestamp,
                "solar_yield": 1000.0,
                "battery_charge": 0.0,
                "battery_discharge": 0.0,
                "battery_soc": 50.0,
                "energy_import": 0.0,
                "energy_export": 800.0,  # Exporting
            }
        )

        # During export: total_energy stays constant, total_energy_returned increases
        shelly_data.append(
            {
                "time": timestamp,
                "total_power": -800.0,  # Negative = exporting
                "total_energy": 1000000.0,  # Stays constant
                "total_energy_returned": 500000.0 + (800.0 * elapsed_hours),  # Increases
                "net_total_energy": 500000.0 - (800.0 * elapsed_hours),  # DECREASES (normal!)
                "phase1_voltage": 230.0,
                "phase2_voltage": 230.0,
                "phase3_voltage": 230.0,
                "phase1_current": 1.2,
                "phase2_current": 1.2,
                "phase3_current": 1.2,
                "phase1_pf": -1.0,  # Negative power factor during export
                "phase2_pf": -1.0,
                "phase3_pf": -1.0,
            }
        )

    window_end = base_time + datetime.timedelta(minutes=5)
    result = aggregate_5min_window(checkwatt_data, shelly_data, window_end)

    assert result is not None, "Aggregation should succeed for export scenario"

    # Expected: -800W * 4/60h = -53.33 Wh (negative = export)
    expected_energy = -53.33
    assert (
        abs(result["emeter_diff"] - expected_energy) < 0.5
    ), f"Expected emeter_diff ~{expected_energy} Wh, got {result['emeter_diff']} Wh"

    # Verify average power is negative (export)
    assert result["emeter_avg"] < 0, "Average power should be negative during export"


def test_counter_reset_fails_with_insufficient_data():
    """Test that aggregation fails when first data point has counters < 100 Wh."""
    base_time = datetime.datetime(2026, 1, 8, 12, 0, 0, tzinfo=datetime.timezone.utc)

    checkwatt_data = []
    shelly_data = []

    # Create data with very small initial counters (< 100 Wh)
    for i in range(5):
        timestamp = base_time + datetime.timedelta(minutes=i)

        checkwatt_data.append(
            {
                "time": timestamp,
                "solar_yield": 500.0,
                "battery_charge": 0.0,
                "battery_discharge": 0.0,
                "battery_soc": 50.0,
                "energy_import": 1000.0,
                "energy_export": 0.0,
            }
        )

        shelly_data.append(
            {
                "time": timestamp,
                "total_power": 1000.0,
                "total_energy": 50.0 + i,  # < 100 Wh - insufficient data
                "total_energy_returned": 30.0 + i,  # < 100 Wh
                "net_total_energy": 20.0 + i,
                "phase1_voltage": 230.0,
                "phase2_voltage": 230.0,
                "phase3_voltage": 230.0,
                "phase1_current": 1.5,
                "phase2_current": 1.5,
                "phase3_current": 1.5,
                "phase1_pf": 1.0,
                "phase2_pf": 1.0,
                "phase3_pf": 1.0,
            }
        )

    window_end = base_time + datetime.timedelta(minutes=5)
    result = aggregate_5min_window(checkwatt_data, shelly_data, window_end)

    # Should fail due to insufficient data
    assert result is None, "Aggregation should fail when counters < 100 Wh"


def test_counter_reset_at_start_of_window():
    """Test counter reset between minute 0 and minute 1."""
    base_time = datetime.datetime(2026, 1, 8, 12, 0, 0, tzinfo=datetime.timezone.utc)

    checkwatt_data = []
    shelly_data = []

    # Minute 0: High counter values (before reset)
    timestamp = base_time
    checkwatt_data.append(
        {
            "time": timestamp,
            "solar_yield": 500.0,
            "battery_charge": 0.0,
            "battery_discharge": 0.0,
            "battery_soc": 50.0,
            "energy_import": 1000.0,
            "energy_export": 0.0,
        }
    )
    shelly_data.append(
        {
            "time": timestamp,
            "total_power": 1000.0,
            "total_energy": 1000000.0,
            "total_energy_returned": 500000.0,
            "net_total_energy": 500000.0,
            "phase1_voltage": 230.0,
            "phase2_voltage": 230.0,
            "phase3_voltage": 230.0,
            "phase1_current": 1.5,
            "phase2_current": 1.5,
            "phase3_current": 1.5,
            "phase1_pf": 1.0,
            "phase2_pf": 1.0,
            "phase3_pf": 1.0,
        }
    )

    # Minutes 1-4: After reset, counters start from low values
    for i in range(1, 5):
        timestamp = base_time + datetime.timedelta(minutes=i)
        elapsed_hours = (i - 1) / 60.0  # Time since reset

        checkwatt_data.append(
            {
                "time": timestamp,
                "solar_yield": 500.0,
                "battery_charge": 0.0,
                "battery_discharge": 0.0,
                "battery_soc": 50.0,
                "energy_import": 800.0,
                "energy_export": 0.0,
            }
        )

        shelly_data.append(
            {
                "time": timestamp,
                "total_power": 800.0,
                "total_energy": 150.0 + (800.0 * elapsed_hours),
                "total_energy_returned": 120.0,
                "net_total_energy": 30.0 + (800.0 * elapsed_hours),
                "phase1_voltage": 230.0,
                "phase2_voltage": 230.0,
                "phase3_voltage": 230.0,
                "phase1_current": 1.2,
                "phase2_current": 1.2,
                "phase3_current": 1.2,
                "phase1_pf": 1.0,
                "phase2_pf": 1.0,
                "phase3_pf": 1.0,
            }
        )

    window_end = base_time + datetime.timedelta(minutes=5)
    result = aggregate_5min_window(checkwatt_data, shelly_data, window_end)

    assert result is not None, "Aggregation should succeed with reset at start"

    # Minutes 0->1 (RESET): avg(1000, 800) = 900W * 1/60h = 15.0 Wh
    # Minutes 1->2: 800W * 1/60h = 13.33 Wh
    # Minutes 2->3: 800W * 1/60h = 13.33 Wh
    # Minutes 3->4: 800W * 1/60h = 13.33 Wh
    # Total: 15.0 + 13.33 + 13.33 + 13.33 = 55.0 Wh
    expected_energy = 55.0
    assert (
        abs(result["emeter_diff"] - expected_energy) < 0.5
    ), f"Expected emeter_diff ~{expected_energy} Wh, got {result['emeter_diff']} Wh"


def test_counter_reset_at_end_of_window():
    """Test counter reset between minute 3 and minute 4 (near end)."""
    base_time = datetime.datetime(2026, 1, 8, 12, 0, 0, tzinfo=datetime.timezone.utc)

    checkwatt_data = []
    shelly_data = []

    # Minutes 0-3: Normal operation at 1000W
    for i in range(4):
        timestamp = base_time + datetime.timedelta(minutes=i)
        elapsed_hours = i / 60.0

        checkwatt_data.append(
            {
                "time": timestamp,
                "solar_yield": 500.0,
                "battery_charge": 0.0,
                "battery_discharge": 0.0,
                "battery_soc": 50.0,
                "energy_import": 1000.0,
                "energy_export": 0.0,
            }
        )

        shelly_data.append(
            {
                "time": timestamp,
                "total_power": 1000.0,
                "total_energy": 1000000.0 + (1000.0 * elapsed_hours),
                "total_energy_returned": 500000.0,
                "net_total_energy": 500000.0 + (1000.0 * elapsed_hours),
                "phase1_voltage": 230.0,
                "phase2_voltage": 230.0,
                "phase3_voltage": 230.0,
                "phase1_current": 1.5,
                "phase2_current": 1.5,
                "phase3_current": 1.5,
                "phase1_pf": 1.0,
                "phase2_pf": 1.0,
                "phase3_pf": 1.0,
            }
        )

    # Minute 4: Counter RESET
    timestamp = base_time + datetime.timedelta(minutes=4)
    checkwatt_data.append(
        {
            "time": timestamp,
            "solar_yield": 500.0,
            "battery_charge": 0.0,
            "battery_discharge": 0.0,
            "battery_soc": 50.0,
            "energy_import": 900.0,
            "energy_export": 0.0,
        }
    )

    shelly_data.append(
        {
            "time": timestamp,
            "total_power": 900.0,
            "total_energy": 150.0,  # Reset to low value
            "total_energy_returned": 120.0,
            "net_total_energy": 30.0,
            "phase1_voltage": 230.0,
            "phase2_voltage": 230.0,
            "phase3_voltage": 230.0,
            "phase1_current": 1.3,
            "phase2_current": 1.3,
            "phase3_current": 1.3,
            "phase1_pf": 1.0,
            "phase2_pf": 1.0,
            "phase3_pf": 1.0,
        }
    )

    window_end = base_time + datetime.timedelta(minutes=5)
    result = aggregate_5min_window(checkwatt_data, shelly_data, window_end)

    assert result is not None, "Aggregation should succeed with reset at end"

    # Minutes 0->1: 1000W * 1/60h = 16.67 Wh
    # Minutes 1->2: 1000W * 1/60h = 16.67 Wh
    # Minutes 2->3: 1000W * 1/60h = 16.67 Wh
    # Minutes 3->4 (RESET): avg(1000, 900) = 950W * 1/60h = 15.83 Wh
    # Total: 16.67 + 16.67 + 16.67 + 15.83 = 65.84 Wh
    expected_energy = 65.84
    assert (
        abs(result["emeter_diff"] - expected_energy) < 0.5
    ), f"Expected emeter_diff ~{expected_energy} Wh, got {result['emeter_diff']} Wh"


def test_missing_shelly_data():
    """Test behavior when Shelly EM3 is unreachable (no data points)."""
    base_time = datetime.datetime(2026, 1, 8, 12, 0, 0, tzinfo=datetime.timezone.utc)

    checkwatt_data = []

    # CheckWatt data available
    for i in range(5):
        timestamp = base_time + datetime.timedelta(minutes=i)
        checkwatt_data.append(
            {
                "time": timestamp,
                "solar_yield": 500.0,
                "battery_charge": 0.0,
                "battery_discharge": 0.0,
                "battery_soc": 50.0,
                "energy_import": 1000.0,
                "energy_export": 0.0,
            }
        )

    # No Shelly data (device unreachable)
    shelly_data = []

    window_end = base_time + datetime.timedelta(minutes=5)
    result = aggregate_5min_window(checkwatt_data, shelly_data, window_end)

    # Aggregation should still succeed with CheckWatt data only
    # emeter fields will be missing but solar/battery data should be present
    assert result is not None, "Aggregation should succeed with only CheckWatt data"
    assert "solar_yield_avg" in result
    assert "emeter_avg" not in result or result.get("emeter_diff") is None


def test_single_shelly_data_point():
    """Test behavior when Shelly EM3 returns only 1 data point."""
    base_time = datetime.datetime(2026, 1, 8, 12, 0, 0, tzinfo=datetime.timezone.utc)

    checkwatt_data = []
    shelly_data = []

    # CheckWatt data available (5 points)
    for i in range(5):
        timestamp = base_time + datetime.timedelta(minutes=i)
        checkwatt_data.append(
            {
                "time": timestamp,
                "solar_yield": 500.0,
                "battery_charge": 0.0,
                "battery_discharge": 0.0,
                "battery_soc": 50.0,
                "energy_import": 1000.0,
                "energy_export": 0.0,
            }
        )

    # Only 1 Shelly data point (insufficient for energy calculation)
    timestamp = base_time
    shelly_data.append(
        {
            "time": timestamp,
            "total_power": 1000.0,
            "total_energy": 1000000.0,
            "total_energy_returned": 500000.0,
            "net_total_energy": 500000.0,
            "phase1_voltage": 230.0,
            "phase2_voltage": 230.0,
            "phase3_voltage": 230.0,
            "phase1_current": 1.5,
            "phase2_current": 1.5,
            "phase3_current": 1.5,
            "phase1_pf": 1.0,
            "phase2_pf": 1.0,
            "phase3_pf": 1.0,
        }
    )

    window_end = base_time + datetime.timedelta(minutes=5)
    result = aggregate_5min_window(checkwatt_data, shelly_data, window_end)

    # Should fail - cannot calculate energy from single point
    assert result is None, "Aggregation should fail with only 1 Shelly data point"
