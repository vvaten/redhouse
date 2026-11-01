#!/usr/bin/env python
"""Measure the internet connection by pinging, as wibatemp's pinglogger did.

Each run sends 20 pings to 8.8.8.8 half a second apart and writes one point
of measurement network, with the field names and types wibatemp wrote.
"""

import argparse
import datetime
import os
import re
import subprocess
import sys
from typing import Optional

import influxdb_client
import numpy as np

from src.common.config import get_config
from src.common.config_validator import ConfigValidator
from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger

logger = setup_logger(__name__, "pinglogger.log")

HOST = "8.8.8.8"
COUNT = 20
INTERVAL_SECONDS = 0.5
# Seconds to wait for a reply, pythonping's default
REPLY_WAIT = 2
# A reply faster than 1 ms is a measuring error, as wibatemp counted it
MIN_RTT_MS = 1.0
RTT_PERCENTILES = (5, 10, 25, 50, 75, 90, 95)

REPLY = re.compile(r"icmp_seq=\d+ .*time=([\d.]+) ms")
SUMMARY = re.compile(r"(\d+) packets transmitted, (\d+) received")


def run_ping() -> str:
    """The output of ping. It exits 1 when replies are lost, which is data.
    The C locale keeps a decimal point in its times and its messages in English."""
    result = subprocess.run(
        ["ping", "-n", "-c", str(COUNT), "-i", str(INTERVAL_SECONDS), "-W", str(REPLY_WAIT), HOST],
        capture_output=True,
        text=True,
        timeout=COUNT * INTERVAL_SECONDS + REPLY_WAIT + 15,
        env={**os.environ, "LC_ALL": "C"},
    )
    return result.stdout + result.stderr


def parse_ping(output: str) -> Optional[dict]:
    """The fields of one point, or None when ping gave no statistics. The
    counts are integers and the ratios floats, as the bucket holds them."""
    summary = SUMMARY.search(output)
    if summary is None or int(summary.group(1)) == 0:
        return None
    sent, returned = int(summary.group(1)), int(summary.group(2))
    lost_ratio = (sent - returned) / sent
    fields: dict = {
        "packet_loss": lost_ratio,
        "packets_lost": lost_ratio,
        "stats_lost_ratio": lost_ratio,
        "stats_packets_lost": sent - returned,
        "stats_packets_returned": returned,
        "stats_packets_sent": sent,
        "stats_success_ratio": returned / sent,
    }

    rtts = []
    for line in output.splitlines():
        match = REPLY.search(line)
        if match is not None and "(DUP!)" not in line and float(match.group(1)) >= MIN_RTT_MS:
            rtts.append(float(match.group(1)))
    if rtts:
        fields["rtt_avg_ms"] = round(float(np.mean(rtts)), 3)
        fields["rtt_std_ms"] = round(float(np.std(rtts)), 3)
        fields["rtt_min_ms"] = round(float(np.min(rtts)), 3)
        for percentile in RTT_PERCENTILES:
            fields[f"rtt_p{percentile}_ms"] = round(float(np.percentile(rtts, percentile)), 3)
        fields["rtt_max_ms"] = round(float(np.max(rtts)), 3)
    return fields


def write_point(fields: dict, timestamp: datetime.datetime) -> None:
    """Write without InfluxClient.write_point, which would make the counts
    floats, but with its guard against staging writing to production."""
    config = get_config()
    warning = ConfigValidator.validate_write(
        bucket=config.influxdb_bucket_network,
        fields=fields,
        strict_mode=ConfigValidator.get_strict_mode(),
    )
    if warning:
        logger.debug(warning)
    point = influxdb_client.Point("network").time(timestamp)
    for name, value in fields.items():
        point = point.field(name, value)
    InfluxClient(config).write_with_retry(
        bucket=config.influxdb_bucket_network, org=config.influxdb_org, record=point
    )


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Ping 8.8.8.8 and log the result to InfluxDB")
    parser.add_argument("--dry-run", action="store_true", help="Ping, but do not write")
    args = parser.parse_args(argv)

    timestamp = datetime.datetime.now(datetime.timezone.utc)
    try:
        output = run_ping()
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.error(f"Cannot run ping: {e}")
        return 1

    fields = parse_ping(output)
    if fields is None:
        logger.error(f"ping gave no statistics: {output.strip()[-300:]}")
        return 1

    if args.dry_run:
        logger.info(f"DRY RUN: would write network point at {timestamp.isoformat()}: {fields}")
        return 0

    try:
        write_point(fields, timestamp)
    except Exception as e:
        logger.error(f"Failed to write ping statistics to InfluxDB: {e}")
        return 1

    logger.info(
        f"Ping {HOST}: {fields['stats_packets_returned']}/{fields['stats_packets_sent']} "
        f"returned, median {fields['rtt_p50_ms'] if 'rtt_p50_ms' in fields else '-'} ms"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
