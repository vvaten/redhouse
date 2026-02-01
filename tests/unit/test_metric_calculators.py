"""
Unit tests for metric calculation functions.

Tests all shared calculation functions used across aggregation pipelines.
"""

from src.aggregation.metric_calculators import (
    calculate_electricity_cost,
    calculate_energy_average,
    calculate_energy_sum,
    calculate_net_grid_power,
    calculate_self_consumption_ratio,
    calculate_self_sufficiency_ratio,
    calculate_total_consumption,
    safe_last,
    safe_mean,
    safe_sum,
    sanitize_power_value,
    validate_power_value,
)


class TestCalculateEnergyAverage:
    """Test calculate_energy_average function."""

    def test_average_with_valid_values(self):
        values = [100.0, 200.0, 300.0]
        result = calculate_energy_average(values)
        assert result == 200.0

    def test_average_with_empty_list(self):
        values = []
        result = calculate_energy_average(values, default=42.0)
        assert result == 42.0

    def test_average_with_none_values(self):
        values = [100.0, None, 200.0, None, 300.0]
        result = calculate_energy_average(values)
        assert result == 200.0

    def test_average_with_all_none_values(self):
        values = [None, None, None]
        result = calculate_energy_average(values, default=10.0)
        assert result == 10.0

    def test_average_with_single_value(self):
        values = [150.0]
        result = calculate_energy_average(values)
        assert result == 150.0


class TestCalculateEnergySum:
    """Test calculate_energy_sum function."""

    def test_energy_sum_5_minutes(self):
        # 1000 W for 5 minutes (300 seconds) should be 1000 * (300/3600) = 83.33 Wh
        result = calculate_energy_sum(1000.0, 300)
        assert abs(result - 83.333333) < 0.001

    def test_energy_sum_1_hour(self):
        # 1000 W for 1 hour (3600 seconds) should be 1000 Wh
        result = calculate_energy_sum(1000.0, 3600)
        assert result == 1000.0

    def test_energy_sum_15_minutes(self):
        # 1200 W for 15 minutes (900 seconds) should be 1200 * (900/3600) = 300 Wh
        result = calculate_energy_sum(1200.0, 900)
        assert result == 300.0

    def test_energy_sum_with_none(self):
        result = calculate_energy_sum(None, 300)
        assert result == 0.0

    def test_energy_sum_zero_power(self):
        result = calculate_energy_sum(0.0, 300)
        assert result == 0.0


class TestCalculateElectricityCost:
    """Test calculate_electricity_cost function."""

    def test_cost_calculation(self):
        # 10 kWh at 15.5 cents/kWh should be 1.55 EUR
        result = calculate_electricity_cost(10.0, 15.5)
        assert abs(result - 1.55) < 0.001

    def test_cost_with_zero_energy(self):
        result = calculate_electricity_cost(0.0, 15.0)
        assert result == 0.0

    def test_cost_with_zero_price(self):
        result = calculate_electricity_cost(10.0, 0.0)
        assert result == 0.0

    def test_cost_with_none_energy(self):
        result = calculate_electricity_cost(None, 15.0)
        assert result == 0.0

    def test_cost_with_none_price(self):
        result = calculate_electricity_cost(10.0, None)
        assert result == 0.0

    def test_cost_with_high_values(self):
        # 100 kWh at 25 cents/kWh should be 25 EUR
        result = calculate_electricity_cost(100.0, 25.0)
        assert result == 25.0


class TestCalculateSelfConsumptionRatio:
    """Test calculate_self_consumption_ratio function."""

    def test_full_self_consumption(self):
        # 5000 Wh solar, 0 Wh export = 100% self-consumption
        result = calculate_self_consumption_ratio(5000.0, 0.0)
        assert result == 100.0

    def test_no_self_consumption(self):
        # 5000 Wh solar, 5000 Wh export = 0% self-consumption
        result = calculate_self_consumption_ratio(5000.0, 5000.0)
        assert result == 0.0

    def test_partial_self_consumption(self):
        # 5000 Wh solar, 1000 Wh export = 80% self-consumption
        result = calculate_self_consumption_ratio(5000.0, 1000.0)
        assert result == 80.0

    def test_zero_solar_yield(self):
        result = calculate_self_consumption_ratio(0.0, 0.0)
        assert result == 0.0

    def test_none_solar_yield(self):
        result = calculate_self_consumption_ratio(None, 100.0)
        assert result == 0.0

    def test_none_export(self):
        # 5000 Wh solar, None export (treated as 0) = 100% self-consumption
        result = calculate_self_consumption_ratio(5000.0, None)
        assert result == 100.0


class TestCalculateSelfSufficiencyRatio:
    """Test calculate_self_sufficiency_ratio function."""

    def test_full_self_sufficiency(self):
        # 5000 Wh consumption, 0 Wh import = 100% self-sufficiency
        result = calculate_self_sufficiency_ratio(5000.0, 0.0)
        assert result == 100.0

    def test_no_self_sufficiency(self):
        # 5000 Wh consumption, 5000 Wh import = 0% self-sufficiency
        result = calculate_self_sufficiency_ratio(5000.0, 5000.0)
        assert result == 0.0

    def test_partial_self_sufficiency(self):
        # 5000 Wh consumption, 1000 Wh import = 80% self-sufficiency
        result = calculate_self_sufficiency_ratio(5000.0, 1000.0)
        assert result == 80.0

    def test_zero_consumption(self):
        result = calculate_self_sufficiency_ratio(0.0, 0.0)
        assert result == 0.0

    def test_none_consumption(self):
        result = calculate_self_sufficiency_ratio(None, 100.0)
        assert result == 0.0

    def test_none_import(self):
        # 5000 Wh consumption, None import (treated as 0) = 100% self-sufficiency
        result = calculate_self_sufficiency_ratio(5000.0, None)
        assert result == 100.0


class TestSafeMean:
    """Test safe_mean function."""

    def test_mean_with_valid_values(self):
        values = [10.0, 20.0, 30.0]
        result = safe_mean(values)
        assert result == 20.0

    def test_mean_with_empty_list(self):
        values = []
        result = safe_mean(values, default=5.0)
        assert result == 5.0

    def test_mean_with_none_values(self):
        values = [10.0, None, 30.0]
        result = safe_mean(values)
        assert result == 20.0

    def test_mean_with_all_none_values(self):
        values = [None, None]
        result = safe_mean(values, default=7.0)
        assert result == 7.0


class TestSafeLast:
    """Test safe_last function."""

    def test_last_with_valid_values(self):
        values = [10.0, 20.0, 30.0]
        result = safe_last(values)
        assert result == 30.0

    def test_last_with_empty_list(self):
        values = []
        result = safe_last(values, default=5.0)
        assert result == 5.0

    def test_last_with_none_value(self):
        values = [10.0, 20.0, None]
        result = safe_last(values, default=15.0)
        assert result == 15.0

    def test_last_with_single_value(self):
        values = [42.0]
        result = safe_last(values)
        assert result == 42.0


class TestSafeSum:
    """Test safe_sum function."""

    def test_sum_with_valid_values(self):
        values = [10.0, 20.0, 30.0]
        result = safe_sum(values)
        assert result == 60.0

    def test_sum_with_empty_list(self):
        values = []
        result = safe_sum(values, default=5.0)
        assert result == 5.0

    def test_sum_with_none_values(self):
        values = [10.0, None, 30.0]
        result = safe_sum(values)
        assert result == 40.0

    def test_sum_with_all_none_values(self):
        values = [None, None]
        result = safe_sum(values, default=7.0)
        assert result == 7.0


class TestValidatePowerValue:
    """Test validate_power_value function."""

    def test_valid_power_value(self):
        assert validate_power_value(1000.0) is True

    def test_zero_power(self):
        assert validate_power_value(0.0) is True

    def test_negative_power(self):
        assert validate_power_value(-100.0) is False

    def test_excessive_power(self):
        assert validate_power_value(30000.0, max_reasonable_power=25000.0) is False

    def test_none_power(self):
        assert validate_power_value(None) is False

    def test_max_reasonable_power(self):
        assert validate_power_value(25000.0, max_reasonable_power=25000.0) is True
        assert validate_power_value(25001.0, max_reasonable_power=25000.0) is False


class TestSanitizePowerValue:
    """Test sanitize_power_value function."""

    def test_sanitize_valid_value(self):
        result = sanitize_power_value(1000.0)
        assert result == 1000.0

    def test_sanitize_none_value(self):
        result = sanitize_power_value(None)
        assert result == 0.0

    def test_sanitize_negative_value(self):
        result = sanitize_power_value(-100.0)
        assert result == 0.0

    def test_sanitize_excessive_value(self):
        result = sanitize_power_value(30000.0, max_reasonable_power=25000.0)
        assert result == 0.0

    def test_sanitize_zero_value(self):
        result = sanitize_power_value(0.0)
        assert result == 0.0


class TestCalculateNetGridPower:
    """Test calculate_net_grid_power function."""

    def test_net_import(self):
        # 1000 W import, 0 W export = +1000 W (importing)
        result = calculate_net_grid_power(1000.0, 0.0)
        assert result == 1000.0

    def test_net_export(self):
        # 0 W import, 500 W export = -500 W (exporting)
        result = calculate_net_grid_power(0.0, 500.0)
        assert result == -500.0

    def test_balanced(self):
        # 100 W import, 100 W export = 0 W (balanced)
        result = calculate_net_grid_power(100.0, 100.0)
        assert result == 0.0

    def test_with_none_import(self):
        result = calculate_net_grid_power(None, 500.0)
        assert result == -500.0

    def test_with_none_export(self):
        result = calculate_net_grid_power(1000.0, None)
        assert result == 1000.0

    def test_with_both_none(self):
        result = calculate_net_grid_power(None, None)
        assert result == 0.0


class TestCalculateTotalConsumption:
    """Test calculate_total_consumption function."""

    def test_consumption_with_grid_only(self):
        # 1000 W from grid, no solar, no battery
        result = calculate_total_consumption(1000.0, 0.0, 0.0, 0.0)
        assert result == 1000.0

    def test_consumption_with_solar_only(self):
        # 0 W from grid, 1500 W solar, no battery
        result = calculate_total_consumption(0.0, 1500.0, 0.0, 0.0)
        assert result == 1500.0

    def test_consumption_with_battery_discharge(self):
        # 500 W grid + 1000 W solar + 300 W battery discharge
        result = calculate_total_consumption(500.0, 1000.0, 300.0, 0.0)
        assert result == 1800.0

    def test_consumption_with_battery_charge(self):
        # 500 W grid + 1500 W solar - 400 W battery charge
        result = calculate_total_consumption(500.0, 1500.0, 0.0, 400.0)
        assert result == 1600.0

    def test_consumption_with_export(self):
        # -200 W grid (export) + 2000 W solar
        result = calculate_total_consumption(-200.0, 2000.0, 0.0, 0.0)
        assert result == 1800.0

    def test_consumption_with_none_values(self):
        result = calculate_total_consumption(None, None, None, None)
        assert result == 0.0

    def test_consumption_complex_scenario(self):
        # 300 W grid + 1500 W solar + 200 W discharge - 100 W charge
        result = calculate_total_consumption(300.0, 1500.0, 200.0, 100.0)
        assert result == 1900.0
