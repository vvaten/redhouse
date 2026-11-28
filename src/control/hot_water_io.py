"""InfluxDB reads, program reads and points of the hot water tick."""

import datetime
from typing import Optional

from influxdb_client import Point

from src.common.local_time import HELSINKI
from src.control import hot_water_boost as hb
from src.control import hot_water_detect as hd
from src.control.program_fallback import load_valid_program
from src.control.program_shape import PUMP_LOAD
from src.monitoring.program_check import PROGRAM_BASE_DIR, program_path

UTC = datetime.timezone.utc
CYCLE = "hot_water_cycle"
TICK = "hot_water"
LOAD_ID = "hot_water_relay"
CYCLE_FIELDS = (
    "end",
    "kind",
    "expiry",
    "unexpected",
    "start_top",
    "peak_top",
    "start_bottom",
    "heater",
    "slow_start",
    "lost_on_min",
)


def rfc3339(epoch: int) -> str:
    return datetime.datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pivot(influx, flux: str) -> dict:
    """{epoch: {field: value}} of a pivoted query."""
    rows: dict = {}
    for table in influx.query_with_retry(flux):
        for record in table.records:
            t = int(record.get_time().timestamp())
            values = {k: v for k, v in record.values.items() if not k.startswith("_")}
            rows.setdefault(t, {}).update(values)
    return rows


def _minute_flux(bucket: str, measurement: str, fields: list, start: int, stop: int) -> str:
    match = " or ".join(f'r._field == "{f}"' for f in fields)
    return f"""
        from(bucket: "{bucket}")
          |> range(start: {rfc3339(start)}, stop: {rfc3339(stop)})
          |> filter(fn: (r) => r._measurement == "{measurement}" and ({match}))
          |> aggregateWindow(every: 1m, fn: mean, createEmpty: false, timeSrc: "_start")
          |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
    """


def _number(row: dict, key: str) -> Optional[float]:
    return float(row[key]) if key in row and row[key] is not None else None


def read_minutes(influx, config, settings: dict, start: int, stop: int) -> list:
    """Tank and 3EM minutes from start to stop. stop may lie in the future,
    so data dated after now is seen and can be refused as stale."""
    top, bottom = settings["tank_top_field"], settings["tank_bottom_field"]
    tank = _pivot(
        influx,
        _minute_flux(
            config.influxdb_bucket_temperatures, "temperatures", [top, bottom], start, stop
        ),
    )
    phases = [f"phase{i}_power" for i in (1, 2, 3)]
    power = _pivot(
        influx,
        _minute_flux(config.influxdb_bucket_shelly_em3_raw, "shelly_em3", phases, start, stop),
    )
    out = []
    for t in sorted(set(tank) | set(power)):
        row_t = tank[t] if t in tank else {}
        row_p = power[t] if t in power else {}
        watts = [w for f in phases if (w := _number(row_p, f)) is not None]
        kw = tuple(w / 1000 for w in watts) if len(watts) == 3 else None
        out.append(hd.Minute(t, _number(row_t, top), _number(row_t, bottom), kw))
    return out


def read_prices(influx, config, start: int, stop: int) -> dict:
    """{hour start epoch: mean price_total EUR/kWh}."""
    flux = f"""
        from(bucket: "{config.influxdb_bucket_spotprice}")
          |> range(start: {rfc3339(start)}, stop: {rfc3339(stop)})
          |> filter(fn: (r) => r._measurement == "spot" and r._field == "price_total")
          |> aggregateWindow(every: 1h, fn: mean, createEmpty: false, timeSrc: "_start")
    """
    prices = {}
    for table in influx.query_with_retry(flux):
        for record in table.records:
            if record.get_value() is not None:
                prices[int(record.get_time().timestamp())] = float(record.get_value())
    return prices


def read_history(influx, config, since: int, now: int) -> list:
    """The hot_water_cycle rows since then, as dicts with a start key."""
    flux = f"""
        from(bucket: "{config.influxdb_bucket_load_control}")
          |> range(start: {rfc3339(since)}, stop: {rfc3339(now + 60)})
          |> filter(fn: (r) => r._measurement == "{CYCLE}" and r.data_type == "actual")
          |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
    """
    rows = _pivot(influx, flux)
    out = []
    for t in sorted(rows):
        row = dict(rows[t])
        missing = [k for k in CYCLE_FIELDS if k not in row]
        if missing:
            raise ValueError(f"{CYCLE} at {rfc3339(t)} lacks {missing}")
        row["start"] = t
        out.append(row)
    return out


def to_record(row: dict) -> hb.Record:
    expiry = int(row["expiry"])
    return hb.Record(
        start=int(row["start"]),
        end=int(row["end"]),
        start_top=_number(row, "start_top"),
        peak_top=_number(row, "peak_top"),
        heater=bool(row["heater"]),
        kind=str(row["kind"]),
        expiry=expiry if expiry > 0 else None,
        unexpected=bool(row["unexpected"]),
    )


def program_entries(now: int, base_dir: str = PROGRAM_BASE_DIR) -> list:
    """(epoch, command) of the pump from yesterday, today and tomorrow."""
    today = datetime.datetime.fromtimestamp(now, HELSINKI).date()
    entries = []
    for offset in (-1, 0, 1):
        program = load_valid_program(
            program_path(today + datetime.timedelta(days=offset), base_dir)
        )
        if program is None or PUMP_LOAD not in program["loads"]:
            continue
        for entry in program["loads"][PUMP_LOAD]["schedule"]:
            entries.append((int(entry["timestamp"]), str(entry["command"])))
    return sorted(entries)


def command_at(entries: list, t: int) -> Optional[str]:
    due = [c for at, c in entries if at <= t]
    return due[-1] if due else None


def hour_program(entries: list, start: int, hours: int) -> dict:
    """{hour start: command one minute into the hour}."""
    first = start - start % 3600
    out = {}
    for k in range(hours):
        h = first + k * 3600
        command = command_at(entries, h + 60)
        if command is not None:
            out[h] = command
    return out


def _value(v: Optional[float]) -> float:
    return -1.0 if v is None else float(v)


def cycle_point(rec: hb.Record, cycle: Optional[hd.Cycle], lost: int, cause: str) -> Point:
    point = (
        Point(CYCLE)
        .tag("data_type", "actual")
        .field("end", int(rec.end))
        .field("kind", rec.kind)
        .field("expiry", int(rec.expiry) if rec.expiry is not None else 0)
        .field("unexpected", bool(rec.unexpected))
        .field("start_top", _value(rec.start_top))
        .field("peak_top", _value(rec.peak_top))
        .field("heater", bool(rec.heater))
        .field("lost_on_min", int(lost))
        .field("cause", cause)
        .time(datetime.datetime.fromtimestamp(rec.start, UTC))
    )
    if cycle is not None:
        point = (
            point.field("start_bottom", _value(cycle.start_bottom))
            .field("slow_start", bool(cycle.slow_start))
            .field("kwh", float(cycle.kwh))
            .field("eur", float(cycle.eur))
            .field("minutes", int(cycle.minutes))
            .field("reached_62", bool(rec.peak_top is not None and rec.peak_top >= hb.BOOST_DONE_C))
        )
    return point


def relabel_point(row: dict, rec: hb.Record) -> Point:
    """The same cycle point again with the new kind; the old fields stay."""
    return (
        Point(CYCLE)
        .tag("data_type", "actual")
        .field("kind", rec.kind)
        .field("relabelled", True)
        .time(datetime.datetime.fromtimestamp(int(row["start"]), UTC))
    )


def tick_point(now: int, fields: dict) -> Point:
    point = Point(TICK).tag("data_type", "actual")
    for key, value in fields.items():
        point = point.field(key, value)
    return point.time(datetime.datetime.fromtimestamp(now, UTC))


def relay_point(now: int, on: Optional[bool], reason: str, success: bool) -> Point:
    local_date = datetime.datetime.fromtimestamp(now, HELSINKI).date().isoformat()
    return (
        Point("load_control")
        .tag("program_date", local_date)
        .tag("load_id", LOAD_ID)
        .tag("data_type", "actual")
        .field("command", "ON" if on else "OFF")
        .field("is_on", bool(on))
        .field("power_kw", 0.0)
        .field("reason", reason)
        .field("success", success)
        .time(datetime.datetime.fromtimestamp(now, UTC))
    )
