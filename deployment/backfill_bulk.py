"""
Bulk data fetching and processing for analytics backfill.

Pre-fetches a full day of source data in a few queries, then slices locally
into windows -- ~96x fewer queries than the per-window approach. InfluxDB
takes the per-window means of the minute-level sensor data.
"""

import datetime

from influxdb_client import Point

from src.aggregation.analytics_base import WEATHER_FIELDS, WEATHER_FILTER, emeters_point
from src.aggregation.metric_calculators import sanitize_power_value
from src.aggregation.quarter_prices import (
    PRICE_LOOKBACK,
    bill_periods,
    ceil_hour,
    floor_hour,
    spot_points,
)
from src.common.influx_client import InfluxClient

# Old emeters keeps power in Wh/s; emeters_5min keeps it in W
LEGACY_AVG_FIELDS = (
    "solar_yield_avg",
    "consumption_avg",
    "emeter_avg",
    "battery_charge_avg",
    "battery_discharge_avg",
    "energy_import_avg",
    "energy_export_avg",
)
LEGACY_DIFF_FIELDS = ("solar_yield_diff", "consumption_diff", "emeter_diff")
WATTS_PER_WH_PER_S = 3600.0
# CheckWatt power fields (W) and the emeters_5min names they fill. Its grid
# import and export read 0 until 2025-04, so the meter gives those
CHECKWATT_FIELDS = {
    "SolarYield": "solar_yield",
    "BatteryCharge": "battery_charge",
    "BatteryDischarge": "battery_discharge",
}
FIVE_MINUTES_IN_HOURS = 5.0 / 60.0


def bulk_fetch_emeters(client: InfluxClient, config, start, end) -> list:
    """Fetch all emeters_5min data for a range in one query."""
    bucket = config.influxdb_bucket_emeters_5min
    query = f"""
from(bucket: "{bucket}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "energy")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
    tables = client.query_with_retry(query)
    return [emeters_point(record) for table in tables for record in table.records]


def _fill_from_checkwatt(point: dict, checkwatt: dict) -> None:
    """Battery and solar power from CheckWatt, sanitized as emeters_5min does,
    and the consumption from the meter; nothing without the meter values."""
    if point["emeter_avg"] is None or point["emeter_diff"] is None:
        return
    for source, name in CHECKWATT_FIELDS.items():
        power = sanitize_power_value(checkwatt[source], name)
        point[f"{name}_avg"] = power
        point[f"{name}_diff"] = power * FIVE_MINUTES_IN_HOURS
    for kind in ("avg", "diff"):
        point[f"consumption_{kind}"] = (
            point[f"emeter_{kind}"]
            + point[f"solar_yield_{kind}"]
            + point[f"battery_discharge_{kind}"]
            - point[f"battery_charge_{kind}"]
        )


def map_legacy_point(values: dict, checkwatt: dict) -> dict:
    """An old emeters 5-min point in the emeters_5min shape: power in W and
    energy in Wh. CheckWatt fills what the old bucket lacks, and before
    CheckWatt the grid import and export are the two signs of the meter."""
    soc = checkwatt["Battery_SoC"] if "Battery_SoC" in checkwatt else None
    point = {"time": values["_time"], "Battery_SoC": soc}
    for field in LEGACY_AVG_FIELDS:
        value = values.get(field)
        point[field] = value * WATTS_PER_WH_PER_S if value is not None else None
    for field in LEGACY_DIFF_FIELDS:
        point[field] = values.get(field)
    seconds = values.get("ts_diff")
    for kind in ("battery_charge", "battery_discharge"):
        power = values.get(f"{kind}_avg")
        energy = power * seconds if power is not None and seconds is not None else None
        point[f"{kind}_diff"] = energy

    if point["battery_charge_avg"] is None and all(f in checkwatt for f in CHECKWATT_FIELDS):
        _fill_from_checkwatt(point, checkwatt)
    meter = point["emeter_avg"]
    if point["energy_import_avg"] is None and point["energy_export_avg"] is None:
        if meter is not None:
            point["energy_import_avg"] = max(meter, 0.0)
            point["energy_export_avg"] = max(-meter, 0.0)
    return point


def _checkwatt_query(bucket: str, start, end, fields: str, fn: str) -> str:
    return f"""
from(bucket: "{bucket}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "checkwatt" and ({fields}))
  |> aggregateWindow(every: 5m, fn: {fn}, timeSrc: "_start", createEmpty: false)
"""


def bulk_fetch_checkwatt(client: InfluxClient, config, start, end) -> dict:
    """{5-min window start: {field: value}}: the last Battery_SoC and the mean
    power of the CheckWatt fields in that window."""
    bucket = config.influxdb_bucket_checkwatt
    power = " or ".join(f'r._field == "{f}"' for f in CHECKWATT_FIELDS)
    windows: dict = {}
    for fields, fn in (('r._field == "Battery_SoC"', "last"), (power, "mean")):
        query = _checkwatt_query(bucket, start, end, fields, fn)
        for table in client.query_with_retry(query):
            for record in table.records:
                value = record.get_value()
                if value is not None:
                    windows.setdefault(record.get_time(), {})[record.get_field()] = value
    return windows


def bulk_fetch_legacy_emeters(client: InfluxClient, config, start, end) -> list:
    """Old emeters data for a range, mapped to the emeters_5min shape."""
    bucket = config.influxdb_bucket_emeters
    query = f"""
from(bucket: "{bucket}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "energy")
  |> filter(fn: (r) => r._field != "solar_yield_avg_prediction")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
    checkwatt = bulk_fetch_checkwatt(client, config, start, end)
    data = []
    no_consumption = 0
    for table in client.query_with_retry(query):
        for record in table.records:
            # Without CheckWatt data the old point has meter energy only
            if record.values.get("consumption_diff") is None:
                no_consumption += 1
                continue
            time = record.get_time()
            data.append(
                map_legacy_point(record.values, checkwatt[time] if time in checkwatt else {})
            )
    if no_consumption:
        print(f"  NO CONSUMPTION {start.date()}: {no_consumption} old points skipped")
    return sorted(data, key=lambda p: p["time"])


def bulk_fetch_spotprices(client: InfluxClient, config, start, end) -> list:
    """The spot price points of the whole hours that the range touches, with
    the price in force at their start. Points before 2023-09-06 have no
    price_sell; they get one with today's buyback margin."""
    bucket = config.influxdb_bucket_spotprice
    margin = config.spot_prices_config["production_buyback_margin"]
    query = f"""
from(bucket: "{bucket}")
  |> range(start: {(floor_hour(start) - PRICE_LOOKBACK).isoformat()}, stop: {ceil_hour(end).isoformat()})
  |> filter(fn: (r) => r._measurement == "spot")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
    return spot_points(client.query_with_retry(query), margin)


def _window_means(
    client: InfluxClient, query: str, interval_minutes: int, rename=lambda field: field
) -> dict:
    """{window start: {renamed field: mean}}, with InfluxDB taking the mean
    of each window, as the live aggregators do with mean()."""
    query += (
        f"  |> aggregateWindow(every: {interval_minutes}m, fn: mean,"
        ' timeSrc: "_start", createEmpty: false)\n'
    )
    means: dict = {}
    for table in client.query_with_retry(query):
        for record in table.records:
            name = rename(record.get_field())
            means.setdefault(record.get_time(), {})[name] = record.get_value()
    return means


def bulk_fetch_weather(client: InfluxClient, config, start, end, interval_minutes: int) -> dict:
    """The weather means per window of a range."""
    bucket = config.influxdb_bucket_weather
    query = f"""
from(bucket: "{bucket}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "weather")
  |> filter(fn: (r) => {WEATHER_FILTER})
"""
    return _window_means(client, query, interval_minutes, WEATHER_FIELDS.__getitem__)


def bulk_fetch_humidities(client: InfluxClient, config, start, end, interval_minutes: int) -> dict:
    """The humidity means per window of a range, named hum_<sensor>."""
    bucket = config.influxdb_bucket_temperatures
    query = f"""
from(bucket: "{bucket}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "humidities")
"""
    return _window_means(client, query, interval_minutes, lambda field: f"hum_{field}")


def bulk_fetch_temperatures(
    client: InfluxClient, config, start, end, interval_minutes: int
) -> dict:
    """The temperature means per window of a range."""
    bucket = config.influxdb_bucket_temperatures
    query = f"""
from(bucket: "{bucket}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "temperatures")
"""
    return _window_means(client, query, interval_minutes)


def slice_emeters(all_emeters: list, window_start, window_end) -> list:
    """Slice pre-fetched emeters data into a single window."""
    return [p for p in all_emeters if window_start <= p["time"] < window_end]


def _of_window(means: dict, window_start):
    return means[window_start] if window_start in means else None


def fetch_and_process_day(
    client: InfluxClient,
    config,
    aggregator,
    day_start,
    day_end,
    interval_minutes: int,
    iter_windows_fn,
    write_to_influx: bool,
    legacy_emeters: bool = False,
) -> tuple:
    """Fetch all source data for one day, process windows, return points.

    Returns (points_list, ok_count, skip_count).
    """
    fetch_emeters = bulk_fetch_legacy_emeters if legacy_emeters else bulk_fetch_emeters
    emeters = fetch_emeters(client, config, day_start, day_end)
    spotprices = bulk_fetch_spotprices(client, config, day_start, day_end)
    weather = bulk_fetch_weather(client, config, day_start, day_end, interval_minutes)
    temps = bulk_fetch_temperatures(client, config, day_start, day_end, interval_minutes)
    humidities = bulk_fetch_humidities(client, config, day_start, day_end, interval_minutes)

    return process_day_windows(
        aggregator,
        emeters,
        spotprices,
        weather,
        temps,
        humidities,
        day_start,
        day_end,
        interval_minutes,
        iter_windows_fn,
        write_to_influx,
    )


def process_day_windows(
    aggregator,
    emeters: list,
    spotprices: list,
    weather: dict,
    temps: dict,
    humidities: dict,
    day_start,
    day_end,
    interval_minutes: int,
    iter_windows_fn,
    write_to_influx: bool,
) -> tuple:
    """Process all windows within a day from pre-fetched data.

    Returns (points_list, ok_count, skip_count).
    """
    const_interval = datetime.timedelta(minutes=interval_minutes)
    points = []
    day_ok = 0
    day_skip = 0

    for window_end in iter_windows_fn(day_start, day_end, interval_minutes):
        window_start = window_end - const_interval
        emeters_slice = slice_emeters(emeters, window_start, window_end)
        if not emeters_slice:
            day_skip += 1
            continue

        raw_data = {
            "emeters": emeters_slice,
            "spotprice": bill_periods(spotprices, window_start, window_end),
            "weather": _of_window(weather, window_start),
            "temperatures": _of_window(temps, window_start),
            "humidities": _of_window(humidities, window_start),
        }

        metrics = aggregator.calculate_metrics(raw_data, window_start, window_end)
        if not metrics:
            day_skip += 1
            continue
        used = sum(1 for p in emeters_slice if p["consumption_diff"] is not None)
        if used < interval_minutes // 5:
            print(f"  PARTIAL {window_start.isoformat()} {used}/{interval_minutes // 5}")
        if "net_cost" not in metrics:
            # A rewrite keeps the costs an earlier run stored for this window
            print(f"  NO PRICE {window_start.isoformat()}")

        if write_to_influx:
            point = Point("analytics")
            for field_name, value in metrics.items():
                if field_name != "time" and value is not None:
                    point.field(field_name, value)
            point.time(window_start)
            points.append(point)

        day_ok += 1

    return points, day_ok, day_skip
