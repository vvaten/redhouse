"""
Bulk data fetching and processing for analytics backfill.

Pre-fetches a full day of source data in 4 queries, then slices locally
into windows -- ~96x fewer queries than the per-window approach.
"""

import datetime

from src.common.influx_client import InfluxClient


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


def bulk_fetch_spotprices(client: InfluxClient, config, start, end) -> dict:
    """Fetch all spot prices for a range. Returns {hour_timestamp: price_dict}."""
    bucket = config.influxdb_bucket_spotprice
    query = f"""
from(bucket: "{bucket}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "spot")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
    tables = client.query_with_retry(query)
    prices = {}
    for table in tables:
        for record in table.records:
            t = record.get_time()
            hour_key = t.replace(minute=0, second=0, microsecond=0)
            prices[hour_key] = {
                "price_total": record.values.get("price_total"),
                "price_sell": record.values.get("price_sell"),
                "price_withtax": record.values.get("price_withtax"),
            }
    return prices


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
) -> tuple:
    """Fetch all source data for one day, process windows, return points.

    Returns (points_list, ok_count, skip_count).
    """
    emeters = bulk_fetch_emeters(client, config, day_start, day_end)
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
    spotprices: dict,
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
    from influxdb_client import Point

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

        hour_key = window_end.replace(minute=0, second=0, microsecond=0)
        raw_data = {
            "emeters": emeters_slice,
            "spotprice": spotprices.get(hour_key),
            "weather": avg_field_records(weather, window_start, window_end) or None,
            "temperatures": avg_field_records(temps, window_start, window_end) or None,
            "humidities": _avg_humidities_with_prefix(humidities, window_start, window_end) or None,
        }

        metrics = aggregator.calculate_metrics(raw_data, window_start, window_end)
        if not metrics:
            day_skip += 1
            continue

        if write_to_influx:
            point = Point("analytics")
            for field_name, value in metrics.items():
                if field_name != "time" and value is not None:
                    point.field(field_name, value)
            point.time(window_start)
            points.append(point)

        day_ok += 1

    return points, day_ok, day_skip
