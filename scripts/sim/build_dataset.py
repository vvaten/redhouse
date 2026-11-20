#!/usr/bin/env python
"""Build the offline simulation dataset from InfluxDB (read only).

Writes gzipped CSV files to E:/redhouse_data/sim/data (or --out): hourly
and quarter-hour analytics, raw garage and tank temperatures, the 3EM
minute data, the CheckWatt battery minutes and the executed pump commands.
Queries run one month (minute data: one day) at a time.

Usage: python scripts/sim/build_dataset.py [--out DIR] [--only NAME ...]
"""

import argparse
import datetime
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.common.config import get_config  # noqa: E402
from src.common.influx_client import InfluxClient  # noqa: E402

UTC = datetime.timezone.utc
ANALYTICS_FIELDS = [
    "energy_import_avg",
    "energy_export_avg",
    "consumption_avg",
    "emeter_avg",
    "solar_yield_avg",
    "battery_charge_avg",
    "battery_discharge_avg",
    "price_total",
    "price_sell",
    "air_temperature",
    "Autotalli",
]
EM3_FIELDS = [
    "phase1_power",
    "phase2_power",
    "phase3_power",
    "total_power",
    "total_energy",
    "net_total_energy",
]
TANK_FIELDS = ["K\u00e4ytt\u00f6vesi ylh", "K\u00e4ytt\u00f6vesi alh"]
HISTORY_START = datetime.datetime(2022, 11, 1, tzinfo=UTC)
MINUTE_START = datetime.datetime(2026, 10, 14, tzinfo=UTC)


def months(start, stop):
    t = start
    while t < stop:
        nxt = (t.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
        yield t, min(nxt, stop)
        t = nxt


def days(start, stop):
    t = start
    while t < stop:
        yield t, min(t + datetime.timedelta(days=1), stop)
        t += datetime.timedelta(days=1)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def field_filter(fields):
    return " or ".join(f'r._field == "{f}"' for f in fields)


def query_frame(client, flux):
    """Pivoted frame indexed by UTC time, one column per field."""
    frames = client.query_api.query_data_frame(flux)
    if isinstance(frames, list):
        frames = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if frames.empty:
        return frames
    frames = frames.drop(columns=[c for c in ("result", "table") if c in frames.columns])
    return frames.set_index("_time").sort_index()


def collect(client, windows, flux_for, name):
    parts = []
    for start, stop in windows:
        frame = query_frame(client, flux_for(iso(start), iso(stop)))
        print(f"  {name} {start:%Y-%m-%d}: {len(frame)} rows", flush=True)
        if not frame.empty:
            parts.append(frame)
    return pd.concat(parts).sort_index() if parts else pd.DataFrame()


def pivoted(bucket, measurement, fields, extra=""):
    def flux(start, stop):
        return (
            f'from(bucket: "{bucket}") |> range(start: {start}, stop: {stop})'
            f' |> filter(fn: (r) => r._measurement == "{measurement}" and ({field_filter(fields)}))'
            f"{extra}"
            ' |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")'
            ' |> drop(columns: ["_start", "_stop", "_measurement"])'
        )

    return flux


def pump_commands(start, stop):
    return (
        f'from(bucket: "load_control") |> range(start: {start}, stop: {stop})'
        ' |> filter(fn: (r) => r._measurement == "load_control" and r.data_type == "actual"'
        ' and r.load_id == "geothermal_pump" and r._field == "command")'
        ' |> keep(columns: ["_time", "_value"]) |> rename(columns: {_value: "command"})'
    )


def build(name, client, now):
    if name == "hourly":
        flux = pivoted("analytics_1hour", "analytics", ANALYTICS_FIELDS)
        return collect(client, months(HISTORY_START, now), flux, name)
    if name == "quarter":
        flux = pivoted("analytics_15min", "analytics", ANALYTICS_FIELDS)
        return collect(client, months(HISTORY_START, now), flux, name)
    if name == "garage_raw":
        flux = pivoted("temperatures", "temperatures", ["Autotalli"])
        return collect(client, months(HISTORY_START, now), flux, name)
    if name == "tank":
        flux = pivoted("temperatures", "temperatures", TANK_FIELDS)
        return collect(client, days(MINUTE_START, now), flux, name)
    if name == "em3_minute":
        flux = pivoted("shelly_em3_emeters_raw", "shelly_em3", EM3_FIELDS)
        return collect(client, days(MINUTE_START, now), flux, name)
    if name == "battery_minute":
        fields = ["BatteryCharge", "BatteryDischarge", "EnergyImport", "EnergyExport"]
        flux = pivoted("checkwatt", "checkwatt", fields)
        return collect(client, days(MINUTE_START, now), flux, name)
    if name == "pump_commands":
        return collect(
            client, months(MINUTE_START - datetime.timedelta(days=40), now), pump_commands, name
        )
    raise SystemExit(f"Unknown dataset {name}")


NAMES = ["hourly", "quarter", "garage_raw", "tank", "em3_minute", "battery_minute", "pump_commands"]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="E:/redhouse_data/sim/data")
    parser.add_argument("--only", nargs="*", choices=NAMES)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    client = InfluxClient(get_config(), timeout_ms=300000)
    now = datetime.datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    for name in args.only or NAMES:
        frame = build(name, client, now)
        path = out / f"{name}.csv.gz"
        frame.to_csv(path, compression="gzip")
        print(f"{name}: {len(frame)} rows, {frame.index.min()} .. {frame.index.max()} -> {path}")
    client.close()


if __name__ == "__main__":
    main()
