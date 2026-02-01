#!/usr/bin/env python3
"""
5-minute energy meter data aggregation.

Aggregates 1-minute data from CheckWatt and Shelly EM3 into 5-minute windows
with calculated consumption, battery netting, and grid metrics.
"""

import datetime
from typing import Optional

from src.aggregation.aggregation_base import AggregationPipeline
from src.aggregation.metric_calculators import (
    calculate_energy_average,
    calculate_energy_sum,
    calculate_total_consumption,
    safe_last,
    safe_mean,
    sanitize_power_value,
)
from src.common.logger import setup_logger

logger = setup_logger(__name__, "emeters_5min.log")


class Emeters5MinAggregator(AggregationPipeline):
    """5-minute energy meter aggregation pipeline."""

    INTERVAL_SECONDS = 300  # 5 minutes
    MAX_REASONABLE_POWER = 25000.0  # W - max for home installation
    MAX_REASONABLE_DECREASE = 10000.0  # Wh - threshold for counter reset detection

    def fetch_data(self, window_start: datetime.datetime, window_end: datetime.datetime) -> dict:
        """Fetch CheckWatt and Shelly EM3 data for window."""
        checkwatt_data = self._fetch_checkwatt_data(window_start, window_end)
        shelly_data = self._fetch_shelly_data(window_start, window_end)

        return {"checkwatt": checkwatt_data, "shelly": shelly_data}

    def _fetch_checkwatt_data(
        self, start_time: datetime.datetime, end_time: datetime.datetime
    ) -> list:
        """Fetch CheckWatt data from InfluxDB."""
        bucket = self.config.influxdb_bucket_checkwatt

        # Use checkwatt_v2 measurement for test environment
        measurement = "checkwatt_v2" if bucket.endswith("_test") else "checkwatt"

        query = f"""
from(bucket: "{bucket}")
  |> range(start: {start_time.isoformat()}, stop: {end_time.isoformat()})
  |> filter(fn: (r) => r._measurement == "{measurement}")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
"""

        logger.debug(f"Fetching CheckWatt data from {start_time} to {end_time}")

        try:
            tables = self.influx.query_api.query(query, org=self.config.influxdb_org)
            data = []
            for table in tables:
                for record in table.records:
                    data.append(
                        {
                            "time": record.get_time(),
                            "battery_charge": record.values.get("BatteryCharge", 0.0),
                            "battery_discharge": record.values.get("BatteryDischarge", 0.0),
                            "battery_soc": record.values.get("Battery_SoC", 0.0),
                            "energy_import": record.values.get("EnergyImport", 0.0),
                            "energy_export": record.values.get("EnergyExport", 0.0),
                            "solar_yield": record.values.get("SolarYield", 0.0),
                        }
                    )

            logger.info(f"Fetched {len(data)} CheckWatt data points")
            return data

        except Exception as e:
            logger.error(f"Error fetching CheckWatt data: {e}")
            return []

    def _fetch_shelly_data(
        self, start_time: datetime.datetime, end_time: datetime.datetime
    ) -> list:
        """Fetch Shelly EM3 data from InfluxDB."""
        bucket = self.config.influxdb_bucket_shelly_em3_raw

        query = f"""
from(bucket: "{bucket}")
  |> range(start: {start_time.isoformat()}, stop: {end_time.isoformat()})
  |> filter(fn: (r) => r._measurement == "shelly_em3")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
"""

        logger.debug(f"Fetching Shelly EM3 data from {start_time} to {end_time}")

        try:
            tables = self.influx.query_api.query(query, org=self.config.influxdb_org)
            data = []
            for table in tables:
                for record in table.records:
                    data.append(
                        {
                            "time": record.get_time(),
                            "total_power": record.values.get("total_power", 0.0),
                            "net_total_energy": record.values.get("net_total_energy", 0.0),
                            "total_energy": record.values.get("total_energy", 0.0),
                            "total_energy_returned": record.values.get(
                                "total_energy_returned", 0.0
                            ),
                            "phase1_voltage": record.values.get("phase1_voltage", 0.0),
                            "phase2_voltage": record.values.get("phase2_voltage", 0.0),
                            "phase3_voltage": record.values.get("phase3_voltage", 0.0),
                            "phase1_current": record.values.get("phase1_current", 0.0),
                            "phase2_current": record.values.get("phase2_current", 0.0),
                            "phase3_current": record.values.get("phase3_current", 0.0),
                            "phase1_pf": record.values.get("phase1_pf", 0.0),
                            "phase2_pf": record.values.get("phase2_pf", 0.0),
                            "phase3_pf": record.values.get("phase3_pf", 0.0),
                        }
                    )

            logger.info(f"Fetched {len(data)} Shelly EM3 data points")
            return data

        except Exception as e:
            logger.error(f"Error fetching Shelly EM3 data: {e}")
            return []

    def validate_data(self, raw_data: dict) -> bool:
        """Validate that we have sufficient data."""
        checkwatt_data = raw_data.get("checkwatt", [])
        shelly_data = raw_data.get("shelly", [])

        if not checkwatt_data and not shelly_data:
            logger.warning("No data available for aggregation")
            return False

        # For Shelly data, need at least 2 points to calculate energy difference
        if shelly_data and len(shelly_data) < 2:
            logger.error(
                "Only 1 Shelly data point available, need at least 2 for energy calculation"
            )
            return False

        # Check for missing Shelly data (counter too low)
        if shelly_data and len(shelly_data) >= 2:
            first = shelly_data[0]
            if first["total_energy"] < 100.0 or first["total_energy_returned"] < 100.0:
                logger.error(
                    f"Insufficient Shelly data (total={first['total_energy']:.1f}, "
                    f"returned={first['total_energy_returned']:.1f})"
                )
                return False

        return True

    def calculate_metrics(
        self,
        raw_data: dict,
        window_start: datetime.datetime,
        window_end: datetime.datetime,
    ) -> Optional[dict]:
        """Calculate 5-minute aggregated metrics."""
        metrics = {}

        checkwatt_data = raw_data.get("checkwatt", [])
        shelly_data = raw_data.get("shelly", [])

        # Calculate CheckWatt metrics
        if checkwatt_data:
            checkwatt_metrics = self._calculate_checkwatt_metrics(checkwatt_data)
            metrics.update(checkwatt_metrics)

        # Calculate Shelly EM3 metrics
        if shelly_data:
            shelly_metrics = self._calculate_shelly_metrics(shelly_data)
            if shelly_metrics is None:
                # Critical error in Shelly calculation
                return None
            metrics.update(shelly_metrics)

        # Calculate derived metrics (consumption)
        if metrics:
            derived = self._calculate_derived_metrics(metrics)
            metrics.update(derived)

        logger.info(
            f"Aggregated 5-min window: {len(checkwatt_data)} CW points, "
            f"{len(shelly_data)} Shelly points"
        )

        return metrics if metrics else None

    def _calculate_checkwatt_metrics(self, data: list) -> dict:
        """Calculate metrics from CheckWatt data."""
        metrics = {}

        # Extract power values for averaging
        solar_values = [p["solar_yield"] for p in data]
        battery_charge_values = [p["battery_charge"] for p in data]
        battery_discharge_values = [p["battery_discharge"] for p in data]
        import_values = [p["energy_import"] for p in data]
        export_values = [p["energy_export"] for p in data]

        # Calculate averages with sanitization
        avg_solar = sanitize_power_value(
            calculate_energy_average(solar_values),
            "solar",
            self.MAX_REASONABLE_POWER,
            logger,
        )
        avg_battery_charge = sanitize_power_value(
            calculate_energy_average(battery_charge_values),
            "battery_charge",
            self.MAX_REASONABLE_POWER,
            logger,
        )
        avg_battery_discharge = sanitize_power_value(
            calculate_energy_average(battery_discharge_values),
            "battery_discharge",
            self.MAX_REASONABLE_POWER,
            logger,
        )
        avg_import = sanitize_power_value(
            calculate_energy_average(import_values),
            "import",
            self.MAX_REASONABLE_POWER,
            logger,
        )
        avg_export = sanitize_power_value(
            calculate_energy_average(export_values),
            "export",
            self.MAX_REASONABLE_POWER,
            logger,
        )

        # Store average power (W)
        metrics["solar_yield_avg"] = avg_solar
        metrics["battery_charge_avg"] = avg_battery_charge
        metrics["battery_discharge_avg"] = avg_battery_discharge
        metrics["energy_import_avg"] = avg_import
        metrics["energy_export_avg"] = avg_export

        # Calculate energy deltas (Wh over 5 minutes)
        metrics["solar_yield_diff"] = calculate_energy_sum(avg_solar, self.INTERVAL_SECONDS)
        metrics["battery_charge_diff"] = calculate_energy_sum(
            avg_battery_charge, self.INTERVAL_SECONDS
        )
        metrics["battery_discharge_diff"] = calculate_energy_sum(
            avg_battery_discharge, self.INTERVAL_SECONDS
        )

        # Last battery SoC
        metrics["Battery_SoC"] = safe_last([p["battery_soc"] for p in data])

        # Net grid power
        metrics["cw_emeter_avg"] = avg_import - avg_export

        return metrics

    def _calculate_shelly_metrics(self, data: list) -> Optional[dict]:
        """Calculate metrics from Shelly EM3 data."""
        if len(data) < 2:
            return None

        metrics = {}

        # Calculate grid energy with counter reset handling
        energy_result = self._calculate_grid_energy(data)
        if energy_result is None:
            return None

        metrics.update(energy_result)

        # Grid quality metrics
        metrics.update(self._calculate_grid_quality_metrics(data))

        # Returned (exported) energy
        returned_metrics = self._calculate_returned_energy(data)
        if returned_metrics:
            metrics.update(returned_metrics)

        return metrics

    def _calculate_grid_energy(self, data: list) -> Optional[dict]:
        """Calculate grid energy with counter reset handling."""
        first = data[0]
        last = data[-1]

        total_time_diff = (last["time"] - first["time"]).total_seconds()
        if total_time_diff <= 0:
            logger.error("Invalid time range")
            return None

        # Process each consecutive pair to detect counter resets
        total_energy_diff = 0.0

        for i in range(1, len(data)):
            prev = data[i - 1]
            curr = data[i]

            # Check for counter reset
            total_reset = (
                prev["total_energy"] - curr["total_energy"]
            ) > self.MAX_REASONABLE_DECREASE
            returned_reset = (
                prev["total_energy_returned"] - curr["total_energy_returned"]
            ) > self.MAX_REASONABLE_DECREASE

            if total_reset or returned_reset:
                # Counter reset - use averaged power
                avg_power = (prev["total_power"] + curr["total_power"]) / 2.0
                time_diff = (curr["time"] - prev["time"]).total_seconds()
                segment_energy = (avg_power * time_diff) / 3600.0  # Convert to Wh

                logger.warning(
                    f"Counter reset detected between {prev['time']} and {curr['time']}: "
                    f"total {prev['total_energy']:.1f}->{curr['total_energy']:.1f}, "
                    f"returned {prev['total_energy_returned']:.1f}->{curr['total_energy_returned']:.1f}. "
                    f"Using averaged power {avg_power:.1f}W"
                )
            else:
                # Normal case - use counter difference
                segment_energy = curr["net_total_energy"] - prev["net_total_energy"]

            total_energy_diff += segment_energy

        # Convert to average power (W)
        emeter_avg = (total_energy_diff * 3600.0) / total_time_diff

        return {
            "emeter_avg": emeter_avg,
            "emeter_diff": total_energy_diff,
            "ts_diff": total_time_diff,
        }

    def _calculate_grid_quality_metrics(self, data: list) -> dict:
        """Calculate grid voltage, current, and power factor metrics."""
        metrics = {}

        # Voltage average across phases
        voltages = []
        for p in data:
            v1, v2, v3 = p["phase1_voltage"], p["phase2_voltage"], p["phase3_voltage"]
            if v1 > 0 and v2 > 0 and v3 > 0:
                voltages.append((v1 + v2 + v3) / 3.0)

        if voltages:
            metrics["grid_voltage_avg"] = safe_mean(voltages)

        # Current average across phases
        currents = []
        for p in data:
            c1, c2, c3 = p["phase1_current"], p["phase2_current"], p["phase3_current"]
            currents.append((c1 + c2 + c3) / 3.0)

        if currents:
            metrics["grid_current_avg"] = safe_mean(currents)

        # Power factor average across phases
        pfs = []
        for p in data:
            pf1, pf2, pf3 = p["phase1_pf"], p["phase2_pf"], p["phase3_pf"]
            pfs.append((pf1 + pf2 + pf3) / 3.0)

        if pfs:
            metrics["grid_power_factor_avg"] = safe_mean(pfs)

        return metrics

    def _calculate_returned_energy(self, data: list) -> Optional[dict]:
        """Calculate returned (exported) energy metrics."""
        if len(data) < 2:
            return None

        first = data[0]
        last = data[-1]

        returned_start = first["total_energy_returned"]
        returned_end = last["total_energy_returned"]
        time_diff = (last["time"] - first["time"]).total_seconds()

        # Sanity checks
        if returned_start < 100.0 or time_diff <= 0 or returned_end < returned_start:
            reason = "missing data or invalid time"
            if returned_end < returned_start:
                reason = "counter reset detected"
            logger.warning(
                f"Returned energy calculation skipped ({reason}): "
                f"start={returned_start} Wh, end={returned_end} Wh"
            )
            return None

        returned_diff = returned_end - returned_start
        max_reasonable_diff = 5000.0  # Wh for 5-minute window

        if returned_diff > max_reasonable_diff:
            logger.warning(
                f"Suspicious returned energy diff ({returned_diff} Wh over {time_diff}s)"
            )
            return None

        # Convert to average power (W)
        energy_returned_avg = (returned_diff * 3600.0) / time_diff

        return {"energy_returned_avg": energy_returned_avg, "energy_returned_diff": returned_diff}

    def _calculate_derived_metrics(self, metrics: dict) -> dict:
        """Calculate derived metrics like consumption."""
        derived = {}

        # Calculate consumption if we have both grid and CheckWatt data
        if "emeter_avg" in metrics and "solar_yield_avg" in metrics:
            derived["consumption_avg"] = calculate_total_consumption(
                metrics["emeter_avg"],
                metrics["solar_yield_avg"],
                metrics.get("battery_discharge_avg", 0.0),
                metrics.get("battery_charge_avg", 0.0),
            )

            derived["consumption_diff"] = calculate_total_consumption(
                metrics.get("emeter_diff", 0.0),
                metrics.get("solar_yield_diff", 0.0),
                metrics.get("battery_discharge_diff", 0.0),
                metrics.get("battery_charge_diff", 0.0),
            )

        return derived

    def write_results(self, metrics: dict, timestamp: datetime.datetime) -> bool:
        """Write aggregated metrics to InfluxDB."""
        bucket = self.config.influxdb_bucket_emeters_5min

        try:
            success = self.influx.write_point(
                bucket=bucket, measurement="energy", fields=metrics, timestamp=timestamp
            )

            if success:
                logger.info(f"Wrote {len(metrics)} fields to {bucket} at {timestamp}")
            else:
                logger.error(f"Failed to write to {bucket}")

            return success

        except Exception as e:
            logger.error(f"Exception writing data: {e}")
            return False
