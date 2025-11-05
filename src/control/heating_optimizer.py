#!/usr/bin/env python
"""Calculate heating priorities based on electricity prices and solar production."""

import pandas as pd

from src.common.logger import setup_logger

logger = setup_logger(__name__)


class HeatingOptimizer:
    """
    Calculate optimal heating times based on electricity costs and solar availability.

    Takes into account:
    - Electricity spot prices (buy and sell)
    - Solar production forecasts
    - Base load consumption (always-on loads)
    - Heating load power consumption
    """

    # Default base load (average household consumption in kW)
    DEFAULT_BASE_LOAD_KW = 1.0

    # Default heating load (geothermal heat pump in kW)
    DEFAULT_HEATING_LOAD_KW = 3.0

    def __init__(
        self,
        base_load_kw: float = None,
        heating_load_kw: float = None,
        resolution_minutes: int = 60,
    ):
        """
        Initialize heating optimizer.

        Args:
            base_load_kw: Base household load in kW (default: 1.0)
            heating_load_kw: Heating system load in kW (default: 3.0)
            resolution_minutes: Time resolution in minutes (60 = hourly, 15 = quarterly)
        """
        self.base_load_kw = base_load_kw or self.DEFAULT_BASE_LOAD_KW
        self.heating_load_kw = heating_load_kw or self.DEFAULT_HEATING_LOAD_KW
        self.resolution_minutes = resolution_minutes

        if resolution_minutes not in [15, 60]:
            logger.warning(
                f"Unusual resolution {resolution_minutes} minutes. "
                "Typically use 15 (quarterly) or 60 (hourly)"
            )

        logger.info(
            f"Initialized optimizer: base_load={self.base_load_kw}kW, "
            f"heating_load={self.heating_load_kw}kW, "
            f"resolution={self.resolution_minutes}min"
        )

    def calculate_heating_priorities(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate heating priority scores at specified time resolution.

        Lower priority value = better time to heat (cheaper).

        Args:
            df: DataFrame from HeatingDataFetcher with columns:
                - time_floor_local: timestamp
                - solar_yield_avg_prediction: predicted solar (kWh)
                - price_total: electricity buy price (c/kWh)
                - price_sell: electricity sell price (c/kWh)

        Returns:
            DataFrame grouped by resolution with 'heating_prio' column
        """
        if df.empty:
            logger.warning("Empty DataFrame provided to calculate_heating_priorities")
            return pd.DataFrame()

        # Create time floor column based on resolution
        df = df.copy()

        # Check if time_floor_local is in index or columns
        if "time_floor_local" in df.columns:
            time_col = df["time_floor_local"]
        elif df.index.name == "time_floor_local" or isinstance(df.index, pd.DatetimeIndex):
            time_col = df.index
        else:
            raise ValueError("DataFrame must have 'time_floor_local' column or DatetimeIndex")

        if self.resolution_minutes == 15:
            # Round to 15-minute intervals
            df["time_resolution"] = time_col.dt.floor("15T") if hasattr(time_col, 'dt') else time_col.floor("15T")
        else:
            # Use hourly resolution
            df["time_resolution"] = time_col.dt.floor("H") if hasattr(time_col, 'dt') else time_col.floor("H")

        # Group by time resolution and average the values
        grouped = df.groupby("time_resolution")[
            ["solar_yield_avg_prediction", "price_sell", "price_total", "Air temperature"]
        ].mean()

        # Convert solar prediction from W to kWh (multiply by hours = 1)
        # Original code multiplies by 3.6, assuming 5-min intervals * 12 = 1 hour
        grouped["solar_yield_avg_prediction"] = grouped["solar_yield_avg_prediction"] * 3.6

        # Calculate base load solar usage and cost
        grouped["base_load"] = self.base_load_kw
        grouped["solar_yield_for_base"] = grouped[["solar_yield_avg_prediction", "base_load"]].min(
            axis=1
        )
        grouped["bought_electr_for_base"] = grouped["base_load"] - grouped["solar_yield_for_base"]
        grouped["solar_yield_left_after_base"] = (
            grouped["solar_yield_avg_prediction"] - grouped["solar_yield_for_base"]
        )
        grouped["price_for_base"] = (
            grouped["price_total"] * grouped["bought_electr_for_base"]
            + grouped["solar_yield_for_base"] * grouped["price_sell"]
        )

        # Calculate heating load solar usage and cost
        grouped[f"{self.heating_load_kw}kWload"] = self.heating_load_kw

        col = f"{self.heating_load_kw}kWload"
        grouped[f"solar_yield_for_{col}"] = grouped[["solar_yield_left_after_base", col]].min(
            axis=1
        )
        grouped[f"bought_electr_for_{col}"] = grouped[col] - grouped[f"solar_yield_for_{col}"]
        grouped[f"price_for_{col}"] = (
            grouped["price_total"] * grouped[f"bought_electr_for_{col}"]
            + grouped[f"solar_yield_for_{col}"] * grouped["price_sell"]
        )

        # The heating priority is simply the cost of running heating that interval
        grouped["heating_prio"] = grouped[f"price_for_{col}"]

        resolution_label = "hours" if self.resolution_minutes == 60 else "intervals"
        logger.info(
            f"Calculated priorities for {len(grouped)} {resolution_label} "
            f"({self.resolution_minutes} minute resolution)"
        )

        return grouped

    def filter_day_priorities(self, df: pd.DataFrame, date_offset: int = 1) -> pd.DataFrame:
        """
        Filter priorities for a specific day.

        Args:
            df: DataFrame with heating priorities
            date_offset: Day offset (1 = tomorrow, 0 = today)

        Returns:
            Filtered DataFrame for the specified day
        """
        import datetime

        target_day = datetime.datetime.now() + datetime.timedelta(days=date_offset)
        next_day = target_day + datetime.timedelta(days=1)

        target_str = target_day.strftime("%Y-%m-%d")
        next_str = next_day.strftime("%Y-%m-%d")

        day_data = df[(df.index >= target_str) & (df.index < next_str)].copy()

        logger.info(f"Filtered {len(day_data)} hours for day offset {date_offset}")

        return day_data

    def select_cheapest_hours(self, priorities_df: pd.DataFrame, num_hours: float) -> pd.DataFrame:
        """
        Select the cheapest hours to run heating.

        Args:
            priorities_df: DataFrame with 'heating_prio' column
            num_hours: Number of hours to heat (can be fractional, e.g., 6.5)

        Returns:
            DataFrame with selected hours, sorted by priority (cheapest first)
        """
        if priorities_df.empty:
            logger.warning("Empty DataFrame provided to select_cheapest_hours")
            return pd.DataFrame()

        # Sort by priority (lowest = cheapest)
        sorted_df = priorities_df.sort_values("heating_prio")

        # Select required number of hours
        num_hours_int = int(num_hours)
        selected = sorted_df.head(num_hours_int).copy()

        logger.info(
            f"Selected {len(selected)} cheapest hours out of {len(priorities_df)} "
            f"(requested {num_hours} hours)"
        )

        return selected

    def get_priority_range(self, priorities_df: pd.DataFrame) -> tuple[float, float]:
        """
        Get the range of priority values (min and max costs).

        Args:
            priorities_df: DataFrame with 'heating_prio' column

        Returns:
            Tuple of (min_priority, max_priority)
        """
        if priorities_df.empty or "heating_prio" not in priorities_df.columns:
            return (0.0, 0.0)

        min_prio = priorities_df["heating_prio"].min()
        max_prio = priorities_df["heating_prio"].max()

        logger.debug(f"Priority range: {min_prio:.2f} - {max_prio:.2f} c/kWh")

        return (min_prio, max_prio)
