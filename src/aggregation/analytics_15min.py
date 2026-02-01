"""
15-minute analytics aggregator.

Aggregates data from multiple sources:
- emeters_5min: Energy data (3x 5-min windows)
- spotprice: Electricity prices
- weather: Weather forecast
- temperatures: Indoor/outdoor temperatures

Creates analytics_15min bucket with joined data for dashboards and analysis.
Uses the AggregationPipeline base class for structured data processing.
"""

import datetime
from typing import Optional

from src.aggregation.aggregation_base import AggregationPipeline
from src.common.logger import setup_logger

logger = setup_logger(__name__, "analytics_15min.log")


class Analytics15MinAggregator(AggregationPipeline):
    """15-minute analytics aggregation pipeline."""

    INTERVAL_SECONDS = 900  # 15 minutes

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
            logger.warning("No emeters_5min data for 15-min window")
            return False

        return True

    def calculate_metrics(
        self,
        raw_data: dict,
        window_start: datetime.datetime,
        window_end: datetime.datetime,
    ) -> Optional[dict]:
        """Calculate 15-minute aggregated analytics metrics."""
        emeters_data = raw_data.get("emeters", [])
        spotprice = raw_data.get("spotprice")
        weather = raw_data.get("weather")
        temperatures = raw_data.get("temperatures")

        metrics = {}

        # Calculate energy metrics
        energy_metrics = self._calculate_energy_metrics(emeters_data)
        metrics.update(energy_metrics)

        # Calculate cost allocation if we have spot price data
        if spotprice:
            metrics["price_total"] = spotprice.get("price_total")
            metrics["price_sell"] = spotprice.get("price_sell")

            cost_metrics = self._calculate_cost_allocation(metrics, spotprice)
            metrics.update(cost_metrics)

        # Calculate self-consumption ratio
        self_consumption_metrics = self._calculate_self_consumption(metrics)
        metrics.update(self_consumption_metrics)

        # Add weather and temperature fields
        self._add_weather_and_temperature_fields(metrics, weather, temperatures)

        logger.info(
            f"Aggregated 15-min window: {len(emeters_data)} emeters_5min points, "
            f"{len(metrics)} total fields"
        )

        return metrics

    def _calculate_energy_metrics(self, emeters_data: list) -> dict:
        """Aggregate energy data from 3x 5-min windows."""
        from src.aggregation.metric_calculators import safe_last, safe_mean, safe_sum

        metrics = {}

        # Average power values (W) using safe_mean helper
        metrics["solar_yield_avg"] = safe_mean([p.get("solar_yield_avg") for p in emeters_data])
        metrics["consumption_avg"] = safe_mean([p.get("consumption_avg") for p in emeters_data])
        metrics["emeter_avg"] = safe_mean([p.get("emeter_avg") for p in emeters_data])
        metrics["battery_charge_avg"] = safe_mean(
            [p.get("battery_charge_avg") for p in emeters_data]
        )
        metrics["battery_discharge_avg"] = safe_mean(
            [p.get("battery_discharge_avg") for p in emeters_data]
        )

        # Sum energy deltas (Wh) for 15-min totals using safe_sum helper
        metrics["solar_yield_sum"] = safe_sum([p.get("solar_yield_diff") for p in emeters_data])
        metrics["consumption_sum"] = safe_sum([p.get("consumption_diff") for p in emeters_data])
        metrics["emeter_sum"] = safe_sum([p.get("emeter_diff") for p in emeters_data])
        metrics["battery_charge_sum"] = safe_sum(
            [p.get("battery_charge_diff") for p in emeters_data]
        )
        metrics["battery_discharge_sum"] = safe_sum(
            [p.get("battery_discharge_diff") for p in emeters_data]
        )

        # Export is calculated from CheckWatt data
        export_values = []
        for p in emeters_data:
            if p.get("energy_export_avg") is not None:
                # energy_export_avg is in W, convert to Wh for 5 minutes
                export_values.append(p["energy_export_avg"] * (5.0 / 60.0))

        metrics["export_sum"] = safe_sum(export_values)

        # Battery SoC: use last value
        metrics["Battery_SoC"] = safe_last([p.get("Battery_SoC") for p in emeters_data])

        return metrics

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

    def write_results(self, metrics: dict, timestamp: datetime.datetime) -> bool:
        """Write aggregated analytics to InfluxDB."""
        bucket = self.config.influxdb_bucket_analytics_15min

        try:
            from influxdb_client import Point

            point = Point("analytics")
            for field_name, value in metrics.items():
                if field_name != "time" and value is not None:
                    point.field(field_name, value)
            point.time(timestamp)

            self.influx.write_api.write(
                bucket=bucket,
                org=self.config.influxdb_org,
                record=point,
            )
            logger.info(f"Wrote {len(metrics)} fields to {bucket} at {timestamp}")
            return True
        except Exception as e:
            logger.error(f"Error writing to {bucket}: {e}")
            return False
