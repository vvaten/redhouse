"""
Shared metric calculation functions for aggregation pipelines.

These functions provide reusable calculations for energy metrics, costs,
and efficiency ratios across all aggregation intervals.
"""

import logging
from typing import Optional


def calculate_energy_average(values: list, default: float = 0.0) -> float:
    """
    Calculate average power from a list of values.

    Args:
        values: List of power values in Watts
        default: Default value if list is empty

    Returns:
        Average power value or default
    """
    if not values:
        return default

    valid_values = [v for v in values if v is not None]
    if not valid_values:
        return default

    return sum(valid_values) / len(valid_values)


def calculate_energy_sum(average_power: float, interval_seconds: int) -> float:
    """
    Calculate energy sum (Wh) from average power (W).

    Args:
        average_power: Average power in Watts
        interval_seconds: Time interval in seconds

    Returns:
        Energy in Wh
    """
    if average_power is None:
        return 0.0

    # Convert W to Wh: average_power * (seconds / 3600)
    return average_power * (interval_seconds / 3600.0)


def calculate_electricity_cost(energy_kwh: float, price_c_kwh: float) -> float:
    """
    Calculate electricity cost in EUR.

    Args:
        energy_kwh: Energy consumed in kWh
        price_c_kwh: Price in cents per kWh

    Returns:
        Cost in EUR
    """
    if energy_kwh is None or price_c_kwh is None:
        return 0.0
    return (energy_kwh * price_c_kwh) / 100.0


def calculate_self_consumption_ratio(solar_yield_wh: float, export_wh: float) -> float:
    """
    Calculate self-consumption ratio (% of solar used directly).

    Args:
        solar_yield_wh: Total solar production in Wh
        export_wh: Energy exported to grid in Wh

    Returns:
        Self-consumption ratio as percentage (0-100)
    """
    if solar_yield_wh is None or solar_yield_wh == 0:
        return 0.0

    if export_wh is None:
        export_wh = 0.0

    self_consumed = solar_yield_wh - export_wh
    return (self_consumed / solar_yield_wh) * 100.0


def calculate_self_sufficiency_ratio(consumption_wh: float, grid_import_wh: float) -> float:
    """
    Calculate self-sufficiency ratio (% of consumption from solar/battery).

    Args:
        consumption_wh: Total consumption in Wh
        grid_import_wh: Energy imported from grid in Wh

    Returns:
        Self-sufficiency ratio as percentage (0-100)
    """
    if consumption_wh is None or consumption_wh == 0:
        return 0.0

    if grid_import_wh is None:
        grid_import_wh = 0.0

    self_sufficient = consumption_wh - grid_import_wh
    return (self_sufficient / consumption_wh) * 100.0


def safe_mean(values: list, default: float = 0.0) -> float:
    """
    Safely calculate mean of a list of values.

    Args:
        values: List of values
        default: Default value if list is empty or all None

    Returns:
        Mean value or default
    """
    if not values:
        return default

    valid_values = [v for v in values if v is not None]
    if not valid_values:
        return default

    return float(sum(valid_values)) / len(valid_values)


def safe_last(values: list, default: float = 0.0) -> float:
    """
    Safely get last value from a list.

    Args:
        values: List of values
        default: Default value if list is empty

    Returns:
        Last value or default
    """
    if not values:
        return default

    return float(values[-1]) if values[-1] is not None else default


def safe_sum(values: list, default: float = 0.0) -> float:
    """
    Safely calculate sum of a list of values.

    Args:
        values: List of values
        default: Default value if list is empty or all None

    Returns:
        Sum of values or default
    """
    if not values:
        return default

    valid_values = [v for v in values if v is not None]
    if not valid_values:
        return default

    return float(sum(valid_values))


def validate_power_value(value: float, max_reasonable_power: float = 25000.0) -> bool:
    """
    Validate that a power value is within reasonable limits.

    Args:
        value: Power value in Watts
        max_reasonable_power: Maximum reasonable power in Watts

    Returns:
        True if value is reasonable
    """
    if value is None:
        return False

    if value < 0:
        return False

    if value > max_reasonable_power:
        return False

    return True


def sanitize_power_value(
    value: float,
    field_name: str = "unknown",
    max_reasonable_power: float = 25000.0,
    logger: Optional[logging.Logger] = None,
) -> float:
    """
    Sanitize a power value, setting unreasonable values to 0.

    Args:
        value: Power value in Watts
        field_name: Name of field for logging
        max_reasonable_power: Maximum reasonable power in Watts
        logger: Optional logger object for warnings

    Returns:
        Sanitized value (0 if invalid)
    """
    if value is None:
        return 0.0

    if not validate_power_value(value, max_reasonable_power):
        if logger:
            logger.warning(f"Suspicious {field_name} value detected and zeroed: {value:.1f} W")
        return 0.0

    return value


def calculate_net_grid_power(import_power: float, export_power: float) -> float:
    """
    Calculate net grid power (import - export).

    Positive values indicate import, negative values indicate export.

    Args:
        import_power: Grid import power in Watts
        export_power: Grid export power in Watts

    Returns:
        Net grid power in Watts
    """
    import_val = import_power if import_power is not None else 0.0
    export_val = export_power if export_power is not None else 0.0

    return import_val - export_val


def calculate_total_consumption(
    grid_power: float,
    solar_power: float,
    battery_discharge: float,
    battery_charge: float,
) -> float:
    """
    Calculate total consumption from energy flows.

    Consumption = grid + solar + battery_discharge - battery_charge

    Args:
        grid_power: Net grid power in Watts (can be negative)
        solar_power: Solar production in Watts
        battery_discharge: Battery discharge power in Watts
        battery_charge: Battery charge power in Watts

    Returns:
        Total consumption in Watts
    """
    grid = grid_power if grid_power is not None else 0.0
    solar = solar_power if solar_power is not None else 0.0
    bat_discharge = battery_discharge if battery_discharge is not None else 0.0
    bat_charge = battery_charge if battery_charge is not None else 0.0

    return grid + solar + bat_discharge - bat_charge
