#!/usr/bin/env python3
"""
5-minute energy meter data aggregation.

Aggregates 1-minute data from CheckWatt and Shelly EM3 into 5-minute windows
with calculated consumption, battery netting, and grid metrics.
"""

import argparse
import datetime
from typing import Optional

import pytz

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger

logger = setup_logger(__name__, "emeters_5min.log")


def fetch_checkwatt_data(
    client: InfluxClient, start_time: datetime.datetime, end_time: datetime.datetime
) -> dict:
    """
    Fetch CheckWatt data for the time window.

    Args:
        client: InfluxDB client
        start_time: Start of time window
        end_time: End of time window

    Returns:
        Dictionary with field data
    """
    config = get_config()
    bucket = config.influxdb_bucket_checkwatt

    query = f'''
from(bucket: "{bucket}")
  |> range(start: {start_time.isoformat()}, stop: {end_time.isoformat()})
  |> filter(fn: (r) => r._measurement == "checkwatt")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
'''

    logger.debug(f"Fetching CheckWatt data from {start_time} to {end_time}")

    try:
        tables = client.query_api.query(query, org=config.influxdb_org)
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
        return {"checkwatt": data}

    except Exception as e:
        logger.error(f"Error fetching CheckWatt data: {e}")
        return {"checkwatt": []}


def fetch_shelly_em3_data(
    client: InfluxClient, start_time: datetime.datetime, end_time: datetime.datetime
) -> dict:
    """
    Fetch Shelly EM3 data for the time window.

    Args:
        client: InfluxDB client
        start_time: Start of time window
        end_time: End of time window

    Returns:
        Dictionary with field data
    """
    config = get_config()
    bucket = config.influxdb_bucket_shelly_em3_raw

    query = f'''
from(bucket: "{bucket}")
  |> range(start: {start_time.isoformat()}, stop: {end_time.isoformat()})
  |> filter(fn: (r) => r._measurement == "shelly_em3")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
'''

    logger.debug(f"Fetching Shelly EM3 data from {start_time} to {end_time}")

    try:
        tables = client.query_api.query(query, org=config.influxdb_org)
        data = []
        for table in tables:
            for record in table.records:
                data.append(
                    {
                        "time": record.get_time(),
                        "total_power": record.values.get("total_power", 0.0),
                        "net_total_energy": record.values.get("net_total_energy", 0.0),
                        "total_energy": record.values.get("total_energy", 0.0),
                        "total_energy_returned": record.values.get("total_energy_returned", 0.0),
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
        return {"shelly": data}

    except Exception as e:
        logger.error(f"Error fetching Shelly EM3 data: {e}")
        return {"shelly": []}


def aggregate_5min_window(checkwatt_data: list, shelly_data: list, window_end: datetime.datetime) -> Optional[dict]:
    """
    Aggregate 5-minute window data into summary statistics.

    Args:
        checkwatt_data: List of CheckWatt data points
        shelly_data: List of Shelly EM3 data points
        window_end: End time of the 5-minute window

    Returns:
        Dictionary of aggregated fields, or None if insufficient data
    """
    if not checkwatt_data and not shelly_data:
        logger.warning("No data available for aggregation")
        return None

    fields = {}

    # CheckWatt aggregation
    # IMPORTANT: CheckWatt "delta" grouping returns AVERAGE POWER in Watts, not energy in Wh!
    if checkwatt_data:
        # Average the power values (W), treating None as 0
        num_points = len(checkwatt_data)
        avg_solar = sum(p["solar_yield"] or 0.0 for p in checkwatt_data) / num_points
        avg_battery_charge = sum(p["battery_charge"] or 0.0 for p in checkwatt_data) / num_points
        avg_battery_discharge = sum(p["battery_discharge"] or 0.0 for p in checkwatt_data) / num_points
        avg_import = sum(p["energy_import"] or 0.0 for p in checkwatt_data) / num_points
        avg_export = sum(p["energy_export"] or 0.0 for p in checkwatt_data) / num_points

        # Sanity checks for CheckWatt power values
        # Max reasonable power for a typical home installation:
        # - Main fuse: 3x25A @ 230V = ~17 kW sustained
        # - Peak with heat pump, EV, water heater: ~25 kW
        max_reasonable_power = 25000.0  # W

        # Check each average against reasonable limits
        suspicious_values = []
        if avg_solar > max_reasonable_power:
            suspicious_values.append(f"solar={avg_solar:.1f} W")
            avg_solar = 0.0
        if avg_battery_charge > max_reasonable_power:
            suspicious_values.append(f"battery_charge={avg_battery_charge:.1f} W")
            avg_battery_charge = 0.0
        if avg_battery_discharge > max_reasonable_power:
            suspicious_values.append(f"battery_discharge={avg_battery_discharge:.1f} W")
            avg_battery_discharge = 0.0
        if avg_import > max_reasonable_power:
            suspicious_values.append(f"import={avg_import:.1f} W")
            avg_import = 0.0
        if avg_export > max_reasonable_power:
            suspicious_values.append(f"export={avg_export:.1f} W")
            avg_export = 0.0

        if suspicious_values:
            logger.warning(
                f"CheckWatt suspicious values detected and zeroed: {', '.join(suspicious_values)}"
            )

        # Average power (W) is already calculated
        fields["solar_yield_avg"] = avg_solar
        fields["battery_charge_avg"] = avg_battery_charge
        fields["battery_discharge_avg"] = avg_battery_discharge
        fields["energy_import_avg"] = avg_import
        fields["energy_export_avg"] = avg_export

        # Calculate energy deltas (Wh over 5 minutes) from average power
        # Energy (Wh) = Power (W) * time (hours)
        time_hours = 5.0 / 60.0  # 5 minutes in hours
        fields["solar_yield_diff"] = avg_solar * time_hours
        fields["battery_charge_diff"] = avg_battery_charge * time_hours
        fields["battery_discharge_diff"] = avg_battery_discharge * time_hours

        # Last battery SoC
        fields["Battery_SoC"] = checkwatt_data[-1]["battery_soc"]

        # CW emeter average (net grid power)
        fields["cw_emeter_avg"] = fields["energy_import_avg"] - fields["energy_export_avg"]

    # Shelly EM3 aggregation
    if shelly_data:
        # Emeter average: calculate from first and last net_total_energy
        if len(shelly_data) >= 2:
            energy_start = shelly_data[0]["net_total_energy"]
            energy_end = shelly_data[-1]["net_total_energy"]
            time_diff = (shelly_data[-1]["time"] - shelly_data[0]["time"]).total_seconds()

            # Sanity checks for energy calculation
            # 1. If start energy is 0 or very small, we likely have missing data
            # 2. If end < start, counter has reset
            # 3. If diff is negative, counter has reset
            # Don't calculate diff in these cases, use instantaneous power instead
            if energy_start < 100.0 or time_diff <= 0 or energy_end < energy_start:
                # Missing data, counter reset, or invalid time range
                reason = "missing data or invalid time"
                if energy_end < energy_start:
                    reason = "counter reset detected"
                fields["emeter_avg"] = shelly_data[-1]["total_power"]
                fields["emeter_diff"] = 0.0
                fields["ts_diff"] = time_diff if time_diff > 0 else 0.0
                logger.warning(
                    f"Energy calculation skipped ({reason}): start={energy_start} Wh, "
                    f"end={energy_end} Wh, time_diff={time_diff}s, using instantaneous power"
                )
            else:
                # Energy difference in Wh, convert to W
                energy_diff = energy_end - energy_start
                # Additional sanity check: diff should be reasonable for the time period
                # Max expected power ~50kW, so for 5min window, max diff ~4200 Wh
                max_reasonable_diff = 5000.0  # Wh for 5-minute window
                if energy_diff > max_reasonable_diff:
                    logger.warning(
                        f"Suspicious energy diff ({energy_diff} Wh over {time_diff}s), "
                        f"using instantaneous power"
                    )
                    fields["emeter_avg"] = shelly_data[-1]["total_power"]
                    fields["emeter_diff"] = 0.0
                    fields["ts_diff"] = time_diff
                else:
                    fields["emeter_avg"] = (energy_diff * 3600.0) / time_diff
                    fields["emeter_diff"] = energy_diff
                    fields["ts_diff"] = time_diff
        else:
            # Single data point, use instantaneous power
            fields["emeter_avg"] = shelly_data[0]["total_power"]
            fields["emeter_diff"] = 0.0
            fields["ts_diff"] = 0.0

        # Grid voltage average across phases
        voltages = []
        for p in shelly_data:
            v1, v2, v3 = p["phase1_voltage"], p["phase2_voltage"], p["phase3_voltage"]
            if v1 > 0 and v2 > 0 and v3 > 0:
                voltages.append((v1 + v2 + v3) / 3.0)

        if voltages:
            fields["grid_voltage_avg"] = sum(voltages) / len(voltages)

        # Grid current average across phases
        currents = []
        for p in shelly_data:
            c1, c2, c3 = p["phase1_current"], p["phase2_current"], p["phase3_current"]
            currents.append((c1 + c2 + c3) / 3.0)

        if currents:
            fields["grid_current_avg"] = sum(currents) / len(currents)

        # Power factor average across phases
        pfs = []
        for p in shelly_data:
            pf1, pf2, pf3 = p["phase1_pf"], p["phase2_pf"], p["phase3_pf"]
            pfs.append((pf1 + pf2 + pf3) / 3.0)

        if pfs:
            fields["grid_power_factor_avg"] = sum(pfs) / len(pfs)

        # Calculate returned (exported) energy
        if len(shelly_data) >= 2:
            returned_start = shelly_data[0]["total_energy_returned"]
            returned_end = shelly_data[-1]["total_energy_returned"]
            time_diff = (shelly_data[-1]["time"] - shelly_data[0]["time"]).total_seconds()

            # Sanity check for returned energy: check for resets and missing data
            if returned_start < 100.0 or time_diff <= 0 or returned_end < returned_start:
                # Missing data, counter reset, or invalid time range
                reason = "missing data or invalid time"
                if returned_end < returned_start:
                    reason = "counter reset detected"
                logger.warning(
                    f"Returned energy calculation skipped ({reason}): start={returned_start} Wh, "
                    f"end={returned_end} Wh"
                )
            else:
                returned_diff = returned_end - returned_start
                max_reasonable_diff = 5000.0  # Wh for 5-minute window
                if returned_diff > max_reasonable_diff:
                    logger.warning(
                        f"Suspicious returned energy diff ({returned_diff} Wh over {time_diff}s), "
                        f"skipping returned energy calculation"
                    )
                else:
                    # Energy difference in Wh, convert to W
                    fields["energy_returned_avg"] = (returned_diff * 3600.0) / time_diff
                    fields["energy_returned_diff"] = returned_diff

    # Calculate consumption (total = grid + solar + battery_discharge - battery_charge)
    if "emeter_avg" in fields and "solar_yield_avg" in fields:
        fields["consumption_avg"] = (
            fields["emeter_avg"]
            + fields["solar_yield_avg"]
            + fields["battery_discharge_avg"]
            - fields["battery_charge_avg"]
        )

        fields["consumption_diff"] = (
            fields.get("emeter_diff", 0.0)
            + fields["solar_yield_diff"]
            + fields["battery_discharge_diff"]
            - fields["battery_charge_diff"]
        )

    logger.info(
        f"Aggregated 5-min window: {len(checkwatt_data)} CW points, "
        f"{len(shelly_data)} Shelly points"
    )

    return fields


def write_aggregated_data(
    client: InfluxClient, fields: dict, timestamp: datetime.datetime, dry_run: bool = False
) -> bool:
    """
    Write aggregated 5-minute data to InfluxDB.

    Args:
        client: InfluxDB client
        fields: Aggregated field values
        timestamp: Timestamp for the aggregated point
        dry_run: If True, don't write to database

    Returns:
        True if successful
    """
    if dry_run:
        logger.info(f"DRY RUN: Would write {len(fields)} fields to emeters_5min at {timestamp}")
        logger.debug(f"Fields: {fields}")
        return True

    config = get_config()
    bucket = config.get("influxdb_bucket_emeters_5min", "emeters_5min")

    try:
        success = client.write_point(
            bucket=bucket, measurement="energy", fields=fields, timestamp=timestamp
        )

        if success:
            logger.info(f"Wrote aggregated data to {bucket} at {timestamp}")
        else:
            logger.error(f"Failed to write aggregated data to {bucket}")

        return success

    except Exception as e:
        logger.error(f"Exception writing aggregated data: {e}")
        return False


def aggregate_5min(window_end: Optional[datetime.datetime] = None, dry_run: bool = False) -> int:
    """
    Main aggregation function for 5-minute windows.

    Args:
        window_end: End time of window (default: current time rounded to 5-min)
        dry_run: If True, don't write to database

    Returns:
        0 on success, 1 on failure
    """
    logger.info("Starting 5-minute aggregation")

    # Determine time window
    if window_end is None:
        now = datetime.datetime.now(pytz.UTC)
        # Round down to last 5-minute boundary
        minute = (now.minute // 5) * 5
        window_end = now.replace(minute=minute, second=0, microsecond=0)

    window_start = window_end - datetime.timedelta(minutes=5)

    logger.info(f"Aggregating window: {window_start} to {window_end}")

    # Fetch data
    config = get_config()
    client = InfluxClient(config)

    checkwatt_result = fetch_checkwatt_data(client, window_start, window_end)
    shelly_result = fetch_shelly_em3_data(client, window_start, window_end)

    # Aggregate
    aggregated = aggregate_5min_window(
        checkwatt_result.get("checkwatt", []),
        shelly_result.get("shelly", []),
        window_end,
    )

    if aggregated is None:
        logger.warning("No aggregated data produced")
        return 1

    # Write
    success = write_aggregated_data(client, aggregated, window_end, dry_run=dry_run)

    client.close()

    if success:
        logger.info("5-minute aggregation completed successfully")
        return 0
    else:
        logger.error("5-minute aggregation failed")
        return 1


def main():
    """Main entry point for 5-minute aggregation."""
    parser = argparse.ArgumentParser(description="Aggregate 5-minute energy meter data")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write to database, just log what would be done",
    )
    parser.add_argument(
        "--window-end",
        type=str,
        help="End time of window in ISO format (default: current time rounded to 5-min)",
    )

    args = parser.parse_args()

    window_end = None
    if args.window_end:
        window_end = datetime.datetime.fromisoformat(args.window_end)
        if window_end.tzinfo is None:
            window_end = window_end.replace(tzinfo=pytz.UTC)

    exit_code = aggregate_5min(window_end=window_end, dry_run=args.dry_run)
    return exit_code


if __name__ == "__main__":
    import sys

    sys.exit(main())
