#!/usr/bin/env python
"""Power cap update - wrapper script for systemd, hourly.

Counts every completed hour of this month from the Shelly 3EM net
counter, writes data/power_cap_state.json and power_cap points. The
cap never falls inside a month, also when the meter data is short.

Usage:
    python update_power_cap.py [--dry-run]
"""

import argparse
import datetime
import sys
import time
from pathlib import Path

from influxdb_client import Point

from src.common.config import get_config
from src.common.config_validator import ConfigValidator
from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger
from src.control import power_cap as pc

logger = setup_logger(__name__, "power_cap.log")

STATE_FILE = Path(__file__).resolve().parent / "data" / "power_cap_state.json"
UTC = datetime.timezone.utc


def _rfc3339(t: datetime.datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_first(influx, bucket: str, start: datetime.datetime, stop: datetime.datetime) -> dict:
    """The first net counter value (Wh) of each hour, keyed by the hour start.
    The executor switches at :00, so an hour's first point sits at its edge."""
    flux = f"""
        from(bucket: "{bucket}")
          |> range(start: {_rfc3339(start)}, stop: {_rfc3339(stop)})
          |> filter(fn: (r) => r._measurement == "shelly_em3" and r._field == "net_total_energy")
          |> aggregateWindow(every: 1h, fn: first, createEmpty: false, timeSrc: "_start")
    """
    counter = {}
    for table in influx.query_with_retry(flux):
        for record in table.records:
            if record.get_value() is not None:
                counter[record.get_time().astimezone(UTC)] = float(record.get_value())
    return counter


def hour_points(month: str, import_kw: dict, counted: dict) -> list:
    return [
        Point("power_cap")
        .tag("month", month)
        .tag("kind", "hour")
        .field("import_kw", float(import_kw[t]))
        .field("counted_kw", float(kw))
        .time(t)
        for t, kw in sorted(counted.items())
    ]


def cap_point(month: str, cap_kw: float, top: list, settings, now) -> Point:
    point = (
        Point("power_cap")
        .tag("month", month)
        .tag("kind", "cap")
        .field("cap_kw", float(cap_kw))
        .field("floor_kw", float(settings.month_floor_kw))
        .time(now)
    )
    for i, (_, kw) in enumerate(top, start=1):
        point = point.field(f"top{i}_kw", float(kw))
    return point


def month_result(counted_all: dict, month: str, now, settings, stored: dict) -> tuple:
    """(counted hours, cap, top) of a month: new values over stored ones."""
    counted = {**stored, **pc.in_month(counted_all, month, now)}
    cap_kw, top = pc.month_cap(counted, settings)
    return counted, cap_kw, top


def write_points(influx, bucket: str, points: list) -> None:
    warning = ConfigValidator.validate_write(
        bucket=bucket, fields={"power_cap": True}, strict_mode=ConfigValidator.get_strict_mode()
    )
    if warning:
        logger.debug(warning)
    influx.write_with_retry(bucket=bucket, record=points)


def main() -> int:
    parser = argparse.ArgumentParser(description="Update the month's power cap")
    parser.add_argument("--dry-run", action="store_true", help="Write nothing")
    args = parser.parse_args()
    config = get_config()
    settings = pc.load_cap_settings(config.power_cap_section)
    now = datetime.datetime.now(UTC)
    month = pc.month_key(now)
    previous = pc.month_key(now - datetime.timedelta(hours=1))
    start = pc.month_start(now - datetime.timedelta(hours=1))
    influx = InfluxClient(config)
    try:
        first = fetch_first(influx, config.influxdb_bucket_shelly_em3_raw, start, now)
    except Exception as e:
        logger.error(f"3EM counter unavailable, the state stays as it is: {e}")
        return 1
    import_kw = pc.hourly_import_kw(first, settings)
    counted_all = pc.counted_hours(import_kw, settings)
    state = pc.read_state(STATE_FILE)
    counted, cap_kw, top = month_result(
        counted_all, month, now, settings, pc.stored_counted(state, month)
    )
    missing = pc.missing_hours(counted, now)
    if missing:
        logger.warning(f"{len(missing)} hours of {month} have no value, first {missing[0]}")
    logger.info(
        f"{month}: {len(counted)} hours, cap {cap_kw:.2f} kW, top {[round(k, 2) for _, k in top]}"
    )
    points = hour_points(month, {**import_kw}, pc.in_month(counted_all, month, now))
    points.append(cap_point(month, cap_kw, top, settings, now))
    if previous != month:
        # The month's last hour completes only now, after the month has turned
        last = pc.in_month(counted_all, previous, now)
        points.extend(hour_points(previous, import_kw, last))
    if args.dry_run:
        return 0
    pc.write_state(STATE_FILE, pc.state_dict(month, cap_kw, top, counted, time.time()))
    try:
        write_points(influx, config.influxdb_bucket_load_control, points)
    except Exception as e:
        logger.error(f"power_cap points not written: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
