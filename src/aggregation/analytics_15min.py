"""
15-minute analytics aggregator.

Aggregates data from multiple sources:
- emeters_5min: Energy data (3x 5-min windows)
- spotprice: Electricity prices
- weather: Weather forecast
- temperatures: Indoor/outdoor temperatures

Creates analytics_15min bucket with joined data for dashboards and analysis.
Uses the AnalyticsAggregatorBase class for shared functionality.
"""

import datetime
from typing import Optional

from src.aggregation.analytics_base import AnalyticsAggregatorBase
from src.common.logger import setup_logger

logger = setup_logger(__name__, "analytics_15min.log")


class Analytics15MinAggregator(AnalyticsAggregatorBase):
    """15-minute analytics aggregation pipeline."""

    INTERVAL_SECONDS = 900  # 15 minutes

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
