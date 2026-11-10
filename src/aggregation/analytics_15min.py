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
        spot_quarters = raw_data.get("spotprice")
        weather = raw_data.get("weather")
        temperatures = raw_data.get("temperatures")
        humidities = raw_data.get("humidities")

        metrics = self._calculate_energy_metrics(emeters_data)
        if spot_quarters:
            metrics.update(self._prices_and_costs(emeters_data, spot_quarters))

        # Calculate self-consumption ratio
        self_consumption_metrics = self._calculate_self_consumption(metrics)
        metrics.update(self_consumption_metrics)

        # Add weather, temperature, and humidity fields
        self._add_weather_and_temperature_fields(metrics, weather, temperatures, humidities)

        logger.info(
            f"Aggregated 15-min window: {len(emeters_data)} emeters_5min points, "
            f"{len(metrics)} total fields"
        )

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
