"""
15-minute analytics aggregator.

Aggregates data from multiple sources:
- emeters_5min: Energy data (3x 5-min windows)
- spotprice: Electricity prices
- weather: Weather forecast
- temperatures: Indoor/outdoor temperatures

Creates analytics_15min bucket with joined data for dashboards and analysis.
"""

import datetime
import logging
from typing import Optional

import pytz

from src.common.config import get_config
from src.common.influx_client import InfluxClient

logger = logging.getLogger(__name__)


def fetch_emeters_5min_data(
    client: InfluxClient, start_time: datetime.datetime, end_time: datetime.datetime
) -> list:
    """
    Fetch 5-minute energy meter data for aggregation.

    Args:
        client: InfluxDB client
        start_time: Start of time range
        end_time: End of time range

    Returns:
        List of data points with energy fields
    """
    config = get_config()
    bucket = config.influxdb_bucket_emeters_5min

    query = f"""
from(bucket: "{bucket}")
  |> range(start: {start_time.isoformat()}, stop: {end_time.isoformat()})
  |> filter(fn: (r) => r._measurement == "emeters_5min")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
"""

    logger.debug(f"Fetching emeters_5min data from {start_time} to {end_time}")

    try:
        tables = client.query_api.query(query, org=config.influxdb_org)
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
        logger.info(f"Fetched {len(data)} emeters_5min data points")
        return data
    except Exception as e:
        logger.error(f"Error fetching emeters_5min data: {e}")
        return []


def fetch_spotprice_data(client: InfluxClient, window_time: datetime.datetime) -> Optional[dict]:
    """
    Fetch spot price for the given time.

    Args:
        client: InfluxDB client
        window_time: Time of the 15-min window

    Returns:
        Dictionary with price data or None
    """
    config = get_config()
    bucket = config.influxdb_bucket_spotprice

    # Spot prices are hourly, so get the hour containing this 15-min window
    hour_start = window_time.replace(minute=0, second=0, microsecond=0)
    hour_end = hour_start + datetime.timedelta(hours=1)

    query = f"""
from(bucket: "{bucket}")
  |> range(start: {hour_start.isoformat()}, stop: {hour_end.isoformat()})
  |> filter(fn: (r) => r._measurement == "spotprice")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> limit(n: 1)
"""

    logger.debug(f"Fetching spotprice data for hour {hour_start}")

    try:
        tables = client.query_api.query(query, org=config.influxdb_org)
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


def fetch_weather_data(
    client: InfluxClient, start_time: datetime.datetime, end_time: datetime.datetime
) -> Optional[dict]:
    """
    Fetch weather data for the given time range.

    Args:
        client: InfluxDB client
        start_time: Start of time range
        end_time: End of time range

    Returns:
        Dictionary with averaged weather data or None
    """
    config = get_config()
    bucket = config.influxdb_bucket_weather

    query = f"""
from(bucket: "{bucket}")
  |> range(start: {start_time.isoformat()}, stop: {end_time.isoformat()})
  |> filter(fn: (r) => r._measurement == "weather")
  |> filter(fn: (r) => r._field == "air_temperature" or r._field == "cloud_cover" or r._field == "solar_radiation" or r._field == "wind_speed")
  |> mean()
"""

    logger.debug(f"Fetching weather data from {start_time} to {end_time}")

    try:
        tables = client.query_api.query(query, org=config.influxdb_org)
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


def fetch_temperatures_data(
    client: InfluxClient, start_time: datetime.datetime, end_time: datetime.datetime
) -> Optional[dict]:
    """
    Fetch temperature data for the given time range.

    Args:
        client: InfluxDB client
        start_time: Start of time range
        end_time: End of time range

    Returns:
        Dictionary with averaged temperature data or None
    """
    config = get_config()
    bucket = config.influxdb_bucket_temperatures

    query = f"""
from(bucket: "{bucket}")
  |> range(start: {start_time.isoformat()}, stop: {end_time.isoformat()})
  |> filter(fn: (r) => r._measurement == "temperatures")
  |> filter(fn: (r) => r._field == "PaaMH" or r._field == "Ulkolampo" or r._field == "PalMH")
  |> mean()
"""

    logger.debug(f"Fetching temperatures data from {start_time} to {end_time}")

    try:
        tables = client.query_api.query(query, org=config.influxdb_org)
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


def aggregate_15min_window(
    emeters_data: list,
    spotprice: Optional[dict],
    weather: Optional[dict],
    temperatures: Optional[dict],
    window_end: datetime.datetime,
) -> Optional[dict]:
    """
    Aggregate 15-minute window data into analytics summary.

    Args:
        emeters_data: List of emeters_5min data points (should be 3 points)
        spotprice: Spot price data
        weather: Weather data
        temperatures: Temperature data
        window_end: End timestamp of the 15-min window

    Returns:
        Dictionary with aggregated fields, or None if no data
    """
    if not emeters_data:
        logger.warning("No emeters_5min data for 15-min window")
        return None

    fields = {}

    # Aggregate energy data from 3x 5-min windows
    num_points = len(emeters_data)

    # Average power values (W)
    fields["solar_yield_avg"] = sum(p["solar_yield_avg"] or 0.0 for p in emeters_data) / num_points
    fields["consumption_avg"] = sum(p["consumption_avg"] or 0.0 for p in emeters_data) / num_points
    fields["emeter_avg"] = sum(p["emeter_avg"] or 0.0 for p in emeters_data) / num_points
    fields["battery_charge_avg"] = (
        sum(p["battery_charge_avg"] or 0.0 for p in emeters_data) / num_points
    )
    fields["battery_discharge_avg"] = (
        sum(p["battery_discharge_avg"] or 0.0 for p in emeters_data) / num_points
    )

    # Sum energy deltas (Wh) for 15-min totals
    fields["solar_yield_sum"] = sum(p["solar_yield_diff"] or 0.0 for p in emeters_data)
    fields["consumption_sum"] = sum(p["consumption_diff"] or 0.0 for p in emeters_data)
    fields["emeter_sum"] = sum(p["emeter_diff"] or 0.0 for p in emeters_data)
    fields["battery_charge_sum"] = sum(p["battery_charge_diff"] or 0.0 for p in emeters_data)
    fields["battery_discharge_sum"] = sum(p["battery_discharge_diff"] or 0.0 for p in emeters_data)

    # Export is calculated from CheckWatt data
    export_sum = 0.0
    if emeters_data:
        # Sum energy export from CheckWatt
        for p in emeters_data:
            if p.get("energy_export_avg") is not None:
                # energy_export_avg is in W, convert to Wh for 5 minutes
                export_sum += p["energy_export_avg"] * (5.0 / 60.0)

    fields["export_sum"] = export_sum

    # Battery SoC: use last value
    fields["Battery_SoC"] = emeters_data[-1].get("Battery_SoC")

    # Add spot price data
    if spotprice:
        fields["price_total"] = spotprice.get("price_total")
        fields["price_sell"] = spotprice.get("price_sell")

        # Calculate costs (EUR) using priority-based allocation
        price_total = fields.get("price_total")
        price_sell = fields.get("price_sell")

        if price_total is not None and price_sell is not None:
            # Step 1: Solar to consumption (highest priority)
            solar_to_consumption = min(fields["solar_yield_sum"], fields["consumption_sum"])
            fields["solar_to_consumption"] = solar_to_consumption
            fields["solar_direct_value"] = (solar_to_consumption / 1000.0) * (price_total / 100.0)

            # Step 2: Remaining solar to battery charging
            solar_remaining = fields["solar_yield_sum"] - solar_to_consumption
            solar_to_battery = min(solar_remaining, fields["battery_charge_sum"])
            fields["solar_to_battery"] = solar_to_battery

            # Step 3: Remaining solar to export
            solar_to_export = fields["solar_yield_sum"] - solar_to_consumption - solar_to_battery
            fields["solar_to_export"] = solar_to_export
            fields["solar_export_revenue"] = (solar_to_export / 1000.0) * (price_sell / 100.0)

            # Step 4: Battery charging costs
            # Solar to battery: opportunity cost (could have exported)
            fields["battery_charge_from_solar_cost"] = (solar_to_battery / 1000.0) * (
                price_sell / 100.0
            )

            # Grid to battery: actual import cost
            grid_to_battery = fields["battery_charge_sum"] - solar_to_battery
            fields["grid_to_battery"] = grid_to_battery
            fields["battery_charge_from_grid_cost"] = (grid_to_battery / 1000.0) * (
                price_total / 100.0
            )

            fields["battery_charge_total_cost"] = (
                fields["battery_charge_from_solar_cost"] + fields["battery_charge_from_grid_cost"]
            )

            # Step 5: Remaining consumption (after solar direct)
            remaining_consumption = fields["consumption_sum"] - solar_to_consumption

            # Battery discharge to consumption
            battery_to_consumption = min(fields["battery_discharge_sum"], remaining_consumption)
            fields["battery_to_consumption"] = battery_to_consumption
            fields["battery_discharge_value"] = (battery_to_consumption / 1000.0) * (
                price_total / 100.0
            )

            # Battery discharge to export
            battery_to_export = fields["battery_discharge_sum"] - battery_to_consumption
            fields["battery_to_export"] = battery_to_export
            fields["battery_export_revenue"] = (battery_to_export / 1000.0) * (price_sell / 100.0)

            # Step 6: Grid import for remaining consumption
            remaining_consumption_after_battery = remaining_consumption - battery_to_consumption
            fields["grid_import_cost"] = (remaining_consumption_after_battery / 1000.0) * (
                price_total / 100.0
            )

            # Step 7: Battery arbitrage (net benefit/cost)
            fields["battery_arbitrage"] = (
                fields["battery_discharge_value"] + fields["battery_export_revenue"]
            ) - fields["battery_charge_total_cost"]

            # Step 8: Total costs
            fields["total_electricity_cost"] = fields["grid_import_cost"]
            fields["total_solar_savings"] = (
                fields["solar_direct_value"] + fields["solar_export_revenue"]
            )
            fields["net_cost"] = (
                fields["total_electricity_cost"]
                - fields["total_solar_savings"]
                - fields["battery_arbitrage"]
            )

            # Keep old electricity_cost for backwards compatibility
            fields["electricity_cost"] = fields["grid_import_cost"]

    # Calculate self-consumption (solar used directly by loads)
    # This is same as solar_to_consumption calculated above
    if fields["solar_yield_sum"] > 0:
        # Use the priority-based calculation if available
        if "solar_to_consumption" in fields:
            fields["solar_direct_sum"] = fields["solar_to_consumption"]
        else:
            # Fallback: simple calculation
            solar_direct = fields["solar_yield_sum"] - fields["battery_charge_sum"] - export_sum
            fields["solar_direct_sum"] = max(0.0, solar_direct)

        # Self-consumption ratio = solar used directly / total solar * 100
        fields["self_consumption_ratio"] = (
            fields["solar_direct_sum"] / fields["solar_yield_sum"] * 100.0
        )
    else:
        fields["solar_direct_sum"] = 0.0
        fields["self_consumption_ratio"] = 0.0

    # Add weather data
    if weather:
        fields["air_temperature"] = weather.get("air_temperature")
        fields["cloud_cover"] = weather.get("cloud_cover")
        fields["solar_radiation"] = weather.get("solar_radiation")
        fields["wind_speed"] = weather.get("wind_speed")

    # Add temperature data
    if temperatures:
        fields["PaaMH"] = temperatures.get("PaaMH")
        fields["Ulkolampo"] = temperatures.get("Ulkolampo")
        fields["PalMH"] = temperatures.get("PalMH")

    # Add timestamp
    fields["time"] = window_end

    logger.info(
        f"Aggregated 15-min window: {num_points} emeters_5min points, "
        f"{len(fields)} total fields"
    )
    logger.debug(f"Fields: {fields}")

    return fields


def run_aggregation(window_end: datetime.datetime, dry_run: bool = False) -> bool:
    """
    Run 15-minute aggregation for a specific window.

    Args:
        window_end: End timestamp of the 15-min window to aggregate
        dry_run: If True, don't write to InfluxDB

    Returns:
        True if successful, False otherwise
    """
    logger.info("Starting 15-minute analytics aggregation")
    logger.info(
        f"Aggregating window: {window_end - datetime.timedelta(minutes=15)} to {window_end}"
    )

    config = get_config()
    client = InfluxClient(config)

    # Fetch data from all sources
    start_time = window_end - datetime.timedelta(minutes=15)

    emeters_data = fetch_emeters_5min_data(client, start_time, window_end)
    spotprice = fetch_spotprice_data(client, window_end)
    weather = fetch_weather_data(client, start_time, window_end)
    temperatures = fetch_temperatures_data(client, start_time, window_end)

    # Aggregate
    result = aggregate_15min_window(emeters_data, spotprice, weather, temperatures, window_end)

    if result is None:
        logger.error("Failed to aggregate 15-min window")
        return False

    # Write to InfluxDB
    if dry_run:
        logger.info(f"DRY RUN: Would write {len(result)} fields to analytics_15min at {window_end}")
        logger.debug(f"Fields: {result}")
    else:
        try:
            from influxdb_client import Point

            point = Point("analytics")
            for field_name, value in result.items():
                if field_name != "time" and value is not None:
                    point.field(field_name, value)
            point.time(window_end)

            write_api = client.write_api()
            write_api.write(
                bucket=config.influxdb_bucket_analytics_15min,
                org=config.influxdb_org,
                record=point,
            )
            logger.info(f"Wrote analytics to analytics_15min at {window_end}")
        except Exception as e:
            logger.error(f"Error writing to InfluxDB: {e}")
            return False

    logger.info("15-minute analytics aggregation completed successfully")
    return True


def main():
    """Main entry point for 15-minute analytics aggregation."""
    import argparse

    parser = argparse.ArgumentParser(description="15-minute analytics aggregator")
    parser.add_argument(
        "--window-end",
        type=str,
        help="End timestamp of window (ISO format with timezone, e.g. 2026-01-08T10:15:00+00:00)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Don't write to InfluxDB")
    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Determine window end time
    if args.window_end:
        window_end = datetime.datetime.fromisoformat(args.window_end)
    else:
        # Default: process the previous completed 15-min window
        now = datetime.datetime.now(pytz.UTC)
        # Round down to previous 15-min mark
        minutes = (now.minute // 15) * 15
        window_end = now.replace(minute=minutes, second=0, microsecond=0)

    success = run_aggregation(window_end, dry_run=args.dry_run)
    return 0 if success else 1


if __name__ == "__main__":
    exit(main())
