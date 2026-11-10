"""
1-hour analytics aggregator.

Aggregates data from multiple sources:
- emeters_5min: Energy data (12x 5-min windows)
- spotprice: Electricity prices
- weather: Weather forecast
- temperatures: Indoor/outdoor temperatures

Creates analytics_1hour bucket with joined data for dashboards and analysis.
Uses the AnalyticsAggregatorBase class for shared functionality.
"""

import datetime
from typing import Optional

from src.aggregation.analytics_base import AnalyticsAggregatorBase
from src.common.logger import setup_logger

logger = setup_logger(__name__, "analytics_1hour.log")


class Analytics1HourAggregator(AnalyticsAggregatorBase):
    """1-hour analytics aggregation pipeline."""

    INTERVAL_SECONDS = 3600  # 1 hour

    def calculate_metrics(
        self,
        raw_data: dict,
        window_start: datetime.datetime,
        window_end: datetime.datetime,
    ) -> Optional[dict]:
        """Calculate 1-hour aggregated analytics metrics."""
        emeters_data = raw_data.get("emeters", [])
        spot_quarters = raw_data.get("spotprice")
        weather = raw_data.get("weather")
        temperatures = raw_data.get("temperatures")
        humidities = raw_data.get("humidities")

        metrics = self._calculate_energy_metrics(emeters_data)
        if spot_quarters:
            metrics.update(self._prices_and_costs(emeters_data, spot_quarters))

        # Calculate peak power (1-hour specific)
        peak_metrics = self._calculate_peak_power(emeters_data)
        metrics.update(peak_metrics)

        # Calculate self-consumption ratio
        self_consumption_metrics = self._calculate_self_consumption(metrics)
        metrics.update(self_consumption_metrics)

        # Add weather, temperature, and humidity fields
        self._add_weather_and_temperature_fields(metrics, weather, temperatures, humidities)

        logger.info(
            f"Aggregated 1-hour window: {len(emeters_data)} emeters_5min points, "
            f"{len(metrics)} total fields"
        )

        return metrics

    def _calculate_peak_power(self, emeters_data: list) -> dict:
        """Calculate peak power values (max power in 1 hour)."""
        peak_metrics = {}
        peak_metrics["consumption_max"] = max(p["consumption_avg"] or 0.0 for p in emeters_data)
        peak_metrics["solar_yield_max"] = max(p["solar_yield_avg"] or 0.0 for p in emeters_data)
        peak_metrics["grid_power_max"] = max(p["emeter_avg"] or 0.0 for p in emeters_data)
        return peak_metrics

    def write_results(self, metrics: dict, timestamp: datetime.datetime) -> bool:
        """Write aggregated analytics to InfluxDB."""
        bucket = self.config.influxdb_bucket_analytics_1hour

        try:
            from influxdb_client import Point

            point = Point("analytics")
            for field_name, value in metrics.items():
                if field_name != "time" and value is not None:
                    point.field(field_name, value)
            point.time(timestamp)

            self.influx.write_with_retry(
                bucket=bucket,
                org=self.config.influxdb_org,
                record=point,
            )
            logger.info(f"Wrote {len(metrics)} fields to {bucket} at {timestamp}")
            return True
        except Exception as e:
            logger.error(f"Error writing to {bucket}: {e}")
            return False
