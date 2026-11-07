#!/usr/bin/env python
"""Hourly summary of the ping logger's minute points, kept forever in
network_1hour; the raw network bucket keeps 30 days. A run recomputes the
last finished hours. --start and --end backfill a range of UTC days."""

import argparse
import datetime
import math
import sys
from typing import Optional

import influxdb_client

from src.common.config import get_config
from src.common.config_validator import ConfigValidator
from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger

logger = setup_logger(__name__, "aggregate_network_1hour.log")

HOUR = datetime.timedelta(hours=1)
DAY = datetime.timedelta(days=1)
# Hours each run recomputes: the last finished one and the three before it
RECOMPUTED_HOURS = 4
PERCENTILES = (5, 10, 25, 50, 75, 90, 95)
SENT, RETURNED, LOST = "stats_packets_sent", "stats_packets_returned", "stats_packets_lost"
TIMES = ("rtt_avg_ms", "rtt_std_ms", "rtt_min_ms", "rtt_max_ms")


def minutes_query(bucket: str, start: datetime.datetime, stop: datetime.datetime) -> str:
    """One row per minute point, with each field in its own column."""
    return f"""
from(bucket: "{bucket}")
  |> range(start: {start.isoformat()}, stop: {stop.isoformat()})
  |> filter(fn: (r) => r._measurement == "network")
  |> filter(fn: (r) => r._field == "{SENT}" or r._field == "{RETURNED}" or
      r._field == "{LOST}" or r._field =~ /^rtt_/)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""


def _counts(minutes: list) -> dict:
    """The packet counts as integer sums, and the ratios computed from them."""
    sent = int(sum(m[SENT] for m in minutes))
    returned = int(sum(m[RETURNED] for m in minutes))
    return {
        SENT: sent,
        RETURNED: returned,
        LOST: int(sum(m[LOST] for m in minutes)),
        "stats_lost_ratio": (sent - returned) / sent,
        "stats_success_ratio": returned / sent,
        "minutes": len(minutes),
        "minutes_down": sum(1 for m in minutes if m[RETURNED] == 0),
    }


def _times(timed: list) -> dict:
    """Mean and deviation weighted by each minute's replies, so they match
    all replies pooled; a percentile is the mean of the minutes' values."""
    weight = sum(m[RETURNED] for m in timed)
    mean = sum(m["rtt_avg_ms"] * m[RETURNED] for m in timed) / weight
    square = sum((m["rtt_std_ms"] ** 2 + m["rtt_avg_ms"] ** 2) * m[RETURNED] for m in timed)
    fields = {
        "rtt_avg_ms": round(mean, 3),
        "rtt_std_ms": round(math.sqrt(max(square / weight - mean**2, 0.0)), 3),
        # float(): a field's type may not change within a shard
        "rtt_min_ms": float(min(m["rtt_min_ms"] for m in timed)),
        "rtt_max_ms": float(max(m["rtt_max_ms"] for m in timed)),
    }
    for percentile in PERCENTILES:
        name = f"rtt_p{percentile}_ms"
        values = [m[name] for m in timed if name in m]
        if values:
            fields[name] = round(sum(values) / len(values), 3)
    return fields


def summarize(minutes: list) -> dict:
    """The fields of one hour; the times only when a minute had replies."""
    fields = _counts(minutes)
    timed = [m for m in minutes if m[RETURNED] > 0 and all(k in m for k in TIMES)]
    if timed:
        fields.update(_times(timed))
    return fields


def hourly(records: list) -> dict:
    """{hour start: fields} for the minute records, by the hour they fall in."""
    by_hour: dict = {}
    for record in records:
        minute = {k: v for k, v in record.values.items() if v is not None}
        if any(k not in minute for k in (SENT, RETURNED, LOST)) or minute[SENT] == 0:
            continue
        hour = record.get_time().replace(minute=0, second=0, microsecond=0)
        by_hour.setdefault(hour, []).append(minute)
    return {hour: summarize(minutes) for hour, minutes in sorted(by_hour.items())}


def aggregate(
    client: InfluxClient, start: datetime.datetime, stop: datetime.datetime, dry_run: bool
) -> int:
    """Summarize the hours in [start, stop) and write them; returns how many."""
    config = client.config
    tables = client.query_with_retry(minutes_query(config.influxdb_bucket_network, start, stop))
    hours = hourly([record for table in tables for record in table.records])
    if dry_run or not hours:
        return len(hours)
    target = config.influxdb_bucket_network_1hour
    first = next(iter(hours.values()))
    warning = ConfigValidator.validate_write(
        bucket=target, fields=first, strict_mode=ConfigValidator.get_strict_mode()
    )
    if warning:
        logger.debug(warning)
    points = []
    for hour, fields in hours.items():
        point = influxdb_client.Point("network").time(hour)
        for name, value in fields.items():
            point = point.field(name, value)
        points.append(point)
    client.write_with_retry(bucket=target, org=config.influxdb_org, record=points)
    return len(hours)


def _ranges(args: argparse.Namespace, now: datetime.datetime) -> list:
    """The [start, stop) ranges to aggregate: a day each for a backfill."""
    this_hour = now.replace(minute=0, second=0, microsecond=0)
    if args.start is None:
        return [(this_hour - RECOMPUTED_HOURS * HOUR, this_hour)]
    day = datetime.datetime.combine(args.start, datetime.time(), tzinfo=datetime.timezone.utc)
    last = datetime.datetime.combine(args.end, datetime.time(), tzinfo=datetime.timezone.utc)
    ranges = []
    while day <= last and day < this_hour:
        ranges.append((day, min(day + DAY, this_hour)))
        day += DAY
    return ranges


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize the ping data by the hour")
    day = datetime.date.fromisoformat
    parser.add_argument("--start", type=day, help="Backfill from this UTC date, YYYY-MM-DD")
    parser.add_argument("--end", type=day, help="Backfill to this UTC date, YYYY-MM-DD, inclusive")
    parser.add_argument("--dry-run", action="store_true", help="Compute, but do not write")
    args = parser.parse_args(argv)
    if (args.start is None) != (args.end is None):
        parser.error("--start and --end go together")
    if args.start is not None and args.end < args.start:
        parser.error("--end is before --start")

    client = InfluxClient(get_config())
    total = 0
    start = None
    try:
        for start, stop in _ranges(args, datetime.datetime.now(datetime.timezone.utc)):
            written = aggregate(client, start, stop, args.dry_run)
            total += written
            if args.start is not None:
                logger.info(f"{start.date()}: {written} hours")
    except Exception as e:
        # The days before it are written; a rerun from it resumes the backfill
        logger.error(f"Network hourly aggregation failed at {start}: {e}")
        return 1
    finally:
        client.close()
    verb = "would write" if args.dry_run else "wrote"
    logger.info(f"Network hourly aggregation {verb} {total} hours")
    return 0


if __name__ == "__main__":
    sys.exit(main())
