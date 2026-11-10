"""
Bulk data fetching and processing for analytics backfill.

Pre-fetches a full day of source data in a few queries, then slices locally
into windows -- ~96x fewer queries than the per-window approach.
"""

import datetime

from influxdb_client import Point

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
    data = []
    for table in tables:
        for record in table.records:
            data.append({"time": record.get_time(), **record.values})
    return data


def map_legacy_point(values: dict, battery_soc) -> dict:
    """An old emeters 5-min point in the emeters_5min shape: power in W and
    energy in Wh. The old bucket has no battery energy and no SoC."""
    point = {"time": values["_time"], "Battery_SoC": battery_soc}
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
    return point


def bulk_fetch_battery_soc(client: InfluxClient, config, start, end) -> dict:
    """{5-min window start: the last CheckWatt Battery_SoC in that window}."""
    bucket = config.influxdb_bucket_checkwatt
    query = f"""
from(bucket: "{bucket}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "checkwatt" and r._field == "Battery_SoC")
  |> aggregateWindow(every: 5m, fn: last, timeSrc: "_start", createEmpty: false)
"""
    return {
        record.get_time(): record.get_value()
        for table in client.query_with_retry(query)
        for record in table.records
    }


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
    socs = bulk_fetch_battery_soc(client, config, start, end)
    data = []
    no_consumption = 0
    for table in client.query_with_retry(query):
        for record in table.records:
            # Without CheckWatt data the old point has meter energy only
            if record.values.get("consumption_diff") is None:
                no_consumption += 1
                continue
            soc = socs[record.get_time()] if record.get_time() in socs else None
            data.append(map_legacy_point(record.values, soc))
    if no_consumption:
        print(f"  NO CONSUMPTION {start.date()}: {no_consumption} old points skipped")
    return sorted(data, key=lambda p: p["time"])


def bulk_fetch_spotprices(client: InfluxClient, config, start, end) -> list:
    """The spot price points of the whole hours that the range touches, with
    the price in force at their start."""
    bucket = config.influxdb_bucket_spotprice
    query = f"""
from(bucket: "{bucket}")
  |> range(start: {(floor_hour(start) - PRICE_LOOKBACK).isoformat()}, stop: {ceil_hour(end).isoformat()})
  |> filter(fn: (r) => r._measurement == "spot")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
    return spot_points(client.query_with_retry(query))


def _bulk_fetch_field_records(client: InfluxClient, query: str) -> list:
    """Fetch field/value records from a Flux query for local averaging."""
    tables = client.query_with_retry(query)
    data = []
    for table in tables:
        for record in table.records:
            data.append(
                {
                    "time": record.get_time(),
                    "field": record.get_field(),
                    "value": record.get_value(),
                }
            )
    return data


def bulk_fetch_weather(client: InfluxClient, config, start, end) -> list:
    """Fetch all weather data for a range (raw records for local averaging)."""
    bucket = config.influxdb_bucket_weather
    query = f"""
from(bucket: "{bucket}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "weather")
  |> filter(fn: (r) => r._field == "air_temperature" or r._field == "cloud_cover" or r._field == "solar_radiation" or r._field == "wind_speed")
"""
    return _bulk_fetch_field_records(client, query)


def bulk_fetch_humidities(client: InfluxClient, config, start, end) -> list:
    """Fetch all humidity data for a range (raw records for local averaging)."""
    bucket = config.influxdb_bucket_temperatures
    query = f"""
from(bucket: "{bucket}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "humidities")
"""
    return _bulk_fetch_field_records(client, query)


def bulk_fetch_temperatures(client: InfluxClient, config, start, end) -> list:
    """Fetch all temperature data for a range (raw records for local averaging)."""
    bucket = config.influxdb_bucket_temperatures
    query = f"""
from(bucket: "{bucket}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "temperatures")
"""
    return _bulk_fetch_field_records(client, query)


def slice_emeters(all_emeters: list, window_start, window_end) -> list:
    """Slice pre-fetched emeters data into a single window."""
    return [p for p in all_emeters if window_start <= p["time"] < window_end]


def avg_field_records(records: list, window_start, window_end) -> dict:
    """Average field/value records within a window. Returns {field: mean}."""
    in_window = [r for r in records if window_start <= r["time"] < window_end]
    if not in_window:
        return {}
    sums: dict = {}
    counts: dict = {}
    for r in in_window:
        f = r["field"]
        if r["value"] is not None:
            sums[f] = sums.get(f, 0.0) + r["value"]
            counts[f] = counts.get(f, 0) + 1
    return {f: sums[f] / counts[f] for f in sums}


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
    weather = bulk_fetch_weather(client, config, day_start, day_end)
    temps = bulk_fetch_temperatures(client, config, day_start, day_end)
    humidities = bulk_fetch_humidities(client, config, day_start, day_end)

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


def _avg_humidities_with_prefix(records: list, window_start, window_end) -> dict:
    """Average humidity records and add hum_ prefix to match analytics field names."""
    raw = avg_field_records(records, window_start, window_end)
    if not raw:
        return {}
    return {f"hum_{k}": v for k, v in raw.items()}


def process_day_windows(
    aggregator,
    emeters: list,
    spotprices: list,
    weather: list,
    temps: list,
    humidities: list,
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
            "weather": avg_field_records(weather, window_start, window_end) or None,
            "temperatures": avg_field_records(temps, window_start, window_end) or None,
            "humidities": _avg_humidities_with_prefix(humidities, window_start, window_end) or None,
        }

        metrics = aggregator.calculate_metrics(raw_data, window_start, window_end)
        if not metrics:
            day_skip += 1
            continue
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
