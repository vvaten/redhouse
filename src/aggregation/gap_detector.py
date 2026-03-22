"""
Gap detection for aggregation pipelines.

Queries InfluxDB to find missing windows in aggregated data and returns
a list of window_end timestamps that need to be filled.
"""

import datetime

from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger

logger = setup_logger(__name__, "gap_detector.log")


def find_gaps(
    client: InfluxClient,
    bucket: str,
    measurement: str,
    start: datetime.datetime,
    end: datetime.datetime,
    interval_minutes: int,
) -> list[datetime.datetime]:
    """
    Find missing aggregation windows in the given time range.

    Queries the bucket for existing timestamps, then compares against
    the expected set of window boundaries.

    Args:
        client: InfluxDB client
        bucket: Bucket name to check
        measurement: Measurement name to check
        start: Start of range to check (inclusive)
        end: End of range to check (exclusive)
        interval_minutes: Expected interval between data points in minutes

    Returns:
        List of missing window_end timestamps, sorted chronologically
    """
    query = (
        f'from(bucket: "{bucket}")\n'
        f"  |> range(start: {start.isoformat()}, stop: {end.isoformat()})\n"
        f'  |> filter(fn: (r) => r["_measurement"] == "{measurement}")\n'
        f"  |> first()\n"
        f'  |> keep(columns: ["_time"])\n'
    )

    try:
        tables = client.query_api.query(query, org=client.config.influxdb_org)
        existing_times = set()
        for table in tables:
            for record in table.records:
                existing_times.add(record.get_time())
    except Exception as e:
        logger.warning(f"Failed to query for gaps in {bucket}: {e}")
        return []

    # Build expected window boundaries (window_start timestamps)
    const_interval = datetime.timedelta(minutes=interval_minutes)
    expected_end = start + const_interval
    missing = []

    while expected_end <= end:
        # Data is written at window_start
        window_start = expected_end - const_interval
        if window_start not in existing_times:
            missing.append(expected_end)
        expected_end += const_interval

    return missing
