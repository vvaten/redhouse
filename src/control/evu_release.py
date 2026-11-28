"""One load_control point, measurement evu_release, per release of EVU.

The point is for analysis only, and no rule waits on it. Its client makes one
try with a 3 s timeout, so a hung InfluxDB delays a pump command by 3 s at most.
"""

import datetime
import os

from influxdb_client import Point

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger

logger = setup_logger(__name__)

MEASUREMENT = "evu_release"
TIMEOUT_MS = 3_000
SOURCES = ("executor", "pulse", "manual", "boot", "load_guard")


def _staging(bucket: str) -> bool:
    flag = os.getenv("STAGING_MODE", "false").lower() in ("true", "1", "yes")
    return flag or bucket.endswith("_staging")


def build_point(source: str, new_state: str, previous: str, via: str, now: float) -> Point:
    return (
        Point(MEASUREMENT)
        .tag("source", source)
        .field("new_state", new_state)
        .field("previous", previous)
        .field("via", via)
        .time(datetime.datetime.fromtimestamp(now, datetime.timezone.utc))
    )


def log_release(
    source: str, new_state: str, previous: str, via: str, now: float, mock: bool
) -> bool:
    """Write the point; a failure is logged and dropped. A mock pump outside
    staging writes nothing, so a dry run never lands in production data."""
    if source not in SOURCES:
        raise ValueError(f"Unknown EVU release source {source}")
    logger.info(f"EVU released by {source} ({via}): {previous} -> {new_state}")
    try:
        config = get_config()
        bucket = config.influxdb_bucket_load_control
        if mock and not _staging(bucket):
            return False
        client = InfluxClient(config, timeout_ms=TIMEOUT_MS)
        try:
            point = build_point(source, new_state, previous, via, now)
            client.write_api.write(bucket=bucket, org=config.influxdb_org, record=point)
        finally:
            client.close()
        return True
    except Exception as e:
        logger.error(f"evu_release point not written: {e}")
        return False
