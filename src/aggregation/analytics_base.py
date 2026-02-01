"""
Base class for analytics aggregators (15-minute and 1-hour).

Contains shared functionality for fetching data from multiple sources
and calculating cost allocation, self-consumption, and other metrics.
"""

import datetime
from typing import Optional

from src.aggregation.aggregation_base import AggregationPipeline
from src.common.logger import setup_logger

logger = setup_logger(__name__, "analytics_base.log")


class AnalyticsAggregatorBase(AggregationPipeline):
    """Base class for analytics aggregation pipelines."""

    INTERVAL_SECONDS: int  # Must be defined in subclasses

    def fetch_data(self, window_start: datetime.datetime, window_end: datetime.datetime) -> dict:
        """Fetch data from all sources: emeters, spotprice, weather, temperatures."""
        emeters_data = self._fetch_emeters_5min_data(window_start, window_end)
        spotprice = self._fetch_spotprice_data(window_end)
        weather = self._fetch_weather_data(window_start, window_end)
        temperatures = self._fetch_temperatures_data(window_start, window_end)

        return {
            "emeters": emeters_data,
            "spotprice": spotprice,
            "weather": weather,
            "temperatures": temperatures,
        }

    def _fetch_emeters_5min_data(
        self, start_time: datetime.datetime, end_time: datetime.datetime
    ) -> list:
        """Fetch 5-minute energy meter data for aggregation."""
        bucket = self.config.influxdb_bucket_emeters_5min

        query = f"""
from(bucket: "{bucket}")
  |> range(start: {start_time.isoformat()}, stop: {end_time.isoformat()})
  |> filter(fn: (r) => r._measurement == "energy")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
"""

        logger.debug(f"Fetching emeters_5min data from {start_time} to {end_time}")

        try:
            tables = self.influx.query_api.query(query, org=self.config.influxdb_org)
            data = []
            for table in tables:
                for record in table.records:
                    data.append(
                        {
                            "time": record.get_time(),
                            "solar_yield_avg": record.values.get("solar_yield_avg"),
                            "solar_yield_diff": record.values.get("solar_yield_diff"),
                            "consumption_avg": record.values.get("consumption_avg"),
                            "consumption_diff": record.values.get("consumption_diff"),
                            "emeter_avg": record.values.get("emeter_avg"),
                            "emeter_diff": record.values.get("emeter_diff"),
                            "battery_charge_avg": record.values.get("battery_charge_avg"),
                            "battery_charge_diff": record.values.get("battery_charge_diff"),
                            "battery_discharge_avg": record.values.get("battery_discharge_avg"),
                            "battery_discharge_diff": record.values.get("battery_discharge_diff"),
                            "Battery_SoC": record.values.get("Battery_SoC"),
                            "energy_import_avg": record.values.get("energy_import_avg"),
                            "energy_export_avg": record.values.get("energy_export_avg"),
                        }
                    )
            logger.info(f"Fetched {len(data)} data points from {bucket}")
            return data
        except Exception as e:
            logger.error(f"Error fetching data from {bucket}: {e}")
            return []

    def _fetch_spotprice_data(self, window_time: datetime.datetime) -> Optional[dict]:
        """Fetch spot price for the given time (hourly prices)."""
        bucket = self.config.influxdb_bucket_spotprice

        # Spot prices are hourly, so get the hour containing this window
        hour_start = window_time.replace(minute=0, second=0, microsecond=0)
        hour_end = hour_start + datetime.timedelta(hours=1)

        query = f"""
from(bucket: "{bucket}")
  |> range(start: {hour_start.isoformat()}, stop: {hour_end.isoformat()})
  |> filter(fn: (r) => r._measurement == "spot")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> limit(n: 1)
"""

        logger.debug(f"Fetching spotprice data for hour {hour_start}")

        try:
            tables = self.influx.query_api.query(query, org=self.config.influxdb_org)
            for table in tables:
                for record in table.records:
                    return {
                        "price_total": record.values.get("price_total"),
                        "price_sell": record.values.get("price_sell"),
                    }
            logger.debug("No spotprice data found")
            return None
        except Exception as e:
            logger.error(f"Error fetching spotprice data: {e}")
            return None

    def _fetch_weather_data(
        self, start_time: datetime.datetime, end_time: datetime.datetime
    ) -> Optional[dict]:
        """Fetch weather data for the given time range."""
        bucket = self.config.influxdb_bucket_weather

        query = f"""
from(bucket: "{bucket}")
  |> range(start: {start_time.isoformat()}, stop: {end_time.isoformat()})
  |> filter(fn: (r) => r._measurement == "weather")
  |> filter(fn: (r) => r._field == "air_temperature" or r._field == "cloud_cover" or r._field == "solar_radiation" or r._field == "wind_speed")
  |> mean()
"""

        logger.debug(f"Fetching weather data from {start_time} to {end_time}")

        try:
            tables = self.influx.query_api.query(query, org=self.config.influxdb_org)
            weather_data = {}
            for table in tables:
                for record in table.records:
                    field_name = record.get_field()
                    weather_data[field_name] = record.get_value()

            if weather_data:
                logger.debug(f"Fetched weather data: {list(weather_data.keys())}")
                return weather_data

            logger.debug("No weather data found")
            return None
        except Exception as e:
            logger.error(f"Error fetching weather data: {e}")
            return None

    def _fetch_temperatures_data(
        self, start_time: datetime.datetime, end_time: datetime.datetime
    ) -> Optional[dict]:
        """Fetch temperature data for the given time range."""
        bucket = self.config.influxdb_bucket_temperatures

        query = f"""
from(bucket: "{bucket}")
  |> range(start: {start_time.isoformat()}, stop: {end_time.isoformat()})
  |> filter(fn: (r) => r._measurement == "temperatures")
  |> mean()
"""

        logger.debug(f"Fetching temperatures data from {start_time} to {end_time}")

        try:
            tables = self.influx.query_api.query(query, org=self.config.influxdb_org)
            temp_data = {}
            for table in tables:
                for record in table.records:
                    field_name = record.get_field()
                    temp_data[field_name] = record.get_value()

            if temp_data:
                logger.debug(f"Fetched temperature data: {list(temp_data.keys())}")
                return temp_data

            logger.debug("No temperature data found")
            return None
        except Exception as e:
            logger.error(f"Error fetching temperature data: {e}")
            return None

    def validate_data(self, raw_data: dict) -> bool:
        """Validate that we have sufficient data for aggregation."""
        emeters_data = raw_data.get("emeters", [])

        if not emeters_data:
            interval_name = (
                f"{self.INTERVAL_SECONDS // 60}-min" if self.INTERVAL_SECONDS < 3600 else "1-hour"
            )
            logger.warning(f"No emeters_5min data for {interval_name} window")
            return False

        return True

    def _calculate_cost_allocation(self, metrics: dict, spotprice: dict) -> dict:
        """Calculate costs using priority-based energy flow allocation."""
        cost_metrics: dict = {}
        price_total = spotprice.get("price_total")
        price_sell = spotprice.get("price_sell")

        if price_total is None or price_sell is None:
            return cost_metrics

        # Step 1: Solar to consumption (highest priority)
        solar_to_consumption = min(metrics["solar_yield_sum"], metrics["consumption_sum"])
        cost_metrics["solar_to_consumption"] = solar_to_consumption
        cost_metrics["solar_direct_value"] = (solar_to_consumption / 1000.0) * (price_total / 100.0)

        # Step 2: Remaining solar to battery charging
        solar_remaining = metrics["solar_yield_sum"] - solar_to_consumption
        solar_to_battery = min(solar_remaining, metrics["battery_charge_sum"])
        cost_metrics["solar_to_battery"] = solar_to_battery

        # Step 3: Remaining solar to export
        solar_to_export = metrics["solar_yield_sum"] - solar_to_consumption - solar_to_battery
        cost_metrics["solar_to_export"] = solar_to_export
        cost_metrics["solar_export_revenue"] = (solar_to_export / 1000.0) * (price_sell / 100.0)

        # Step 4: Battery charging costs
        # Solar to battery: opportunity cost (could have exported)
        cost_metrics["battery_charge_from_solar_cost"] = (solar_to_battery / 1000.0) * (
            price_sell / 100.0
        )

        # Grid to battery: actual import cost
        grid_to_battery = metrics["battery_charge_sum"] - solar_to_battery
        cost_metrics["grid_to_battery"] = grid_to_battery
        cost_metrics["battery_charge_from_grid_cost"] = (grid_to_battery / 1000.0) * (
            price_total / 100.0
        )

        cost_metrics["battery_charge_total_cost"] = (
            cost_metrics["battery_charge_from_solar_cost"]
            + cost_metrics["battery_charge_from_grid_cost"]
        )

        # Step 5: Remaining consumption (after solar direct)
        remaining_consumption = metrics["consumption_sum"] - solar_to_consumption

        # Battery discharge to consumption
        battery_to_consumption = min(metrics["battery_discharge_sum"], remaining_consumption)
        cost_metrics["battery_to_consumption"] = battery_to_consumption
        cost_metrics["battery_discharge_value"] = (battery_to_consumption / 1000.0) * (
            price_total / 100.0
        )

        # Battery discharge to export
        battery_to_export = metrics["battery_discharge_sum"] - battery_to_consumption
        cost_metrics["battery_to_export"] = battery_to_export
        cost_metrics["battery_export_revenue"] = (battery_to_export / 1000.0) * (price_sell / 100.0)

        # Step 6: Grid import for remaining consumption
        remaining_consumption_after_battery = remaining_consumption - battery_to_consumption
        cost_metrics["grid_import_cost"] = (remaining_consumption_after_battery / 1000.0) * (
            price_total / 100.0
        )

        # Step 7: Battery arbitrage (net benefit/cost)
        cost_metrics["battery_arbitrage"] = (
            cost_metrics["battery_discharge_value"] + cost_metrics["battery_export_revenue"]
        ) - cost_metrics["battery_charge_total_cost"]

        # Step 8: Total costs
        cost_metrics["total_electricity_cost"] = cost_metrics["grid_import_cost"]
        cost_metrics["total_solar_savings"] = (
            cost_metrics["solar_direct_value"] + cost_metrics["solar_export_revenue"]
        )
        cost_metrics["net_cost"] = (
            cost_metrics["total_electricity_cost"]
            - cost_metrics["total_solar_savings"]
            - cost_metrics["battery_arbitrage"]
        )

        # Keep old electricity_cost for backwards compatibility
        cost_metrics["electricity_cost"] = cost_metrics["grid_import_cost"]

        return cost_metrics

    def _calculate_self_consumption(self, metrics: dict) -> dict:
        """Calculate self-consumption ratio (solar used directly by loads)."""
        self_consumption_metrics = {}

        if metrics["solar_yield_sum"] > 0:
            # Use the priority-based calculation if available
            if "solar_to_consumption" in metrics:
                self_consumption_metrics["solar_direct_sum"] = metrics["solar_to_consumption"]
            else:
                # Fallback: simple calculation
                solar_direct = (
                    metrics["solar_yield_sum"]
                    - metrics["battery_charge_sum"]
                    - metrics["export_sum"]
                )
                self_consumption_metrics["solar_direct_sum"] = max(0.0, solar_direct)

            # Self-consumption ratio = solar used directly / total solar * 100
            self_consumption_metrics["self_consumption_ratio"] = (
                self_consumption_metrics["solar_direct_sum"] / metrics["solar_yield_sum"] * 100.0
            )
        else:
            self_consumption_metrics["solar_direct_sum"] = 0.0
            self_consumption_metrics["self_consumption_ratio"] = 0.0

        return self_consumption_metrics

    def _add_weather_and_temperature_fields(
        self, metrics: dict, weather: Optional[dict], temperatures: Optional[dict]
    ) -> None:
        """Add weather and temperature data to metrics."""
        # Add weather data
        if weather:
            metrics["air_temperature"] = weather.get("air_temperature")
            metrics["cloud_cover"] = weather.get("cloud_cover")
            metrics["solar_radiation"] = weather.get("solar_radiation")
            metrics["wind_speed"] = weather.get("wind_speed")

        # Add all temperature data
        if temperatures:
            for field_name, value in temperatures.items():
                metrics[field_name] = value
