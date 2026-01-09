#!/usr/bin/env python
"""CheckWatt battery and solar data collection."""

import asyncio
import datetime
import logging
from typing import Any, Optional

import aiohttp
import influxdb_client

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.json_logger import JSONDataLogger
from src.common.logger import setup_logger

logger = setup_logger(__name__, "checkwatt.log")

# CheckWatt API endpoints
AUTH_URL = "https://api.checkwatt.se/user/Login?audience=eib"
DATA_URL = "https://api.checkwatt.se/datagrouping/series"

# Expected meter data columns in order
CHECKWATT_COLUMNS = [
    "Battery_SoC",
    "BatteryCharge",
    "BatteryDischarge",
    "EnergyImport",
    "EnergyExport",
    "SolarYield",
]


async def get_auth_token(username: str, password: str) -> Optional[str]:
    """
    Get authentication token from CheckWatt API.

    Args:
        username: CheckWatt username (email)
        password: CheckWatt password

    Returns:
        JWT token string, or None if failed
    """
    import base64

    logger.info("Requesting auth token from CheckWatt")

    # Create Basic auth header
    auth_string = f"{username}:{password}"
    auth_bytes = auth_string.encode("ascii")
    auth_b64 = base64.b64encode(auth_bytes).decode("ascii")

    headers = {
        "accept": "application/json, text/plain, */*",
        "authorization": f"Basic {auth_b64}",
        "content-type": "application/json",
    }

    payload = '{"OneTimePassword":""}'

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(AUTH_URL, data=payload, headers=headers) as response:
                if response.status == 200:
                    json_data = await response.json()
                    if "JwtToken" in json_data:
                        logger.info("Successfully obtained auth token")
                        return json_data["JwtToken"]
                    else:
                        logger.error("Auth response missing JwtToken")
                        return None
                else:
                    logger.error(
                        f"Auth request failed with status {response.status}: "
                        f"{await response.text()}"
                    )
                    return None

    except Exception as e:
        logger.error(f"Exception getting auth token: {e}")
        return None


def format_datetime(dt) -> str:
    """
    Format datetime to ISO format string required by API.

    Args:
        dt: datetime object or ISO string

    Returns:
        ISO format string (YYYY-MM-DDTHH:MM:SS)
    """
    if isinstance(dt, str) and len(dt) == 19:
        return dt
    if isinstance(dt, (datetime.datetime, datetime.date)):
        return dt.isoformat()[:19]
    raise ValueError(f"Unknown date format: {type(dt)}")


async def fetch_checkwatt_data(
    auth_token: str, meter_ids: list[str], from_date: str, to_date: str
) -> Optional[dict]:
    """
    Fetch CheckWatt data from API.

    Args:
        auth_token: JWT authentication token
        meter_ids: List of meter IDs to query
        from_date: Start date (ISO format)
        to_date: End date (ISO format)

    Returns:
        JSON data from API, or None if failed
    """
    from_date = format_datetime(from_date)
    to_date = format_datetime(to_date)

    # Build URL with meter IDs
    meter_params = "&".join([f"meterId={mid}" for mid in meter_ids])
    url = f"{DATA_URL}?grouping=delta&" f"fromdate={from_date}&todate={to_date}&" f"{meter_params}"

    logger.info(f"Fetching CheckWatt data from {from_date} to {to_date}")

    headers = {
        "accept": "application/json, text/plain, */*",
        "authorization": f"Bearer {auth_token}",
        "content-type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    json_data = await response.json()
                    logger.info("Successfully fetched CheckWatt data")
                    return json_data
                else:
                    logger.error(
                        f"Data request failed with status {response.status}: "
                        f"{await response.text()}"
                    )
                    return None

    except Exception as e:
        logger.error(f"Exception fetching CheckWatt data: {e}")
        return None


def process_checkwatt_data(json_data: dict) -> list[dict]:
    """
    Process CheckWatt JSON data into InfluxDB format.

    Args:
        json_data: Raw JSON data from CheckWatt API

    Returns:
        List of data points ready for InfluxDB
    """
    logger.info("Processing CheckWatt data")

    # Validate data format
    if json_data.get("Grouping") != "delta":
        raise ValueError(f"Only delta grouping supported, got {json_data.get('Grouping')}")

    if len(json_data.get("Meters", [])) != len(CHECKWATT_COLUMNS):
        raise ValueError(
            f"Expected {len(CHECKWATT_COLUMNS)} meters, " f"got {len(json_data.get('Meters', []))}"
        )

    # Parse start time
    dt = datetime.datetime.fromisoformat(json_data["DateFrom"])
    start_timestamp = int(dt.timestamp())

    # Initialize data points with timestamps (1-minute intervals)
    measurements_soc = json_data["Meters"][0]["Measurements"]
    data_points: list[dict[str, Any]] = []

    for i in range(len(measurements_soc)):
        data_points.append({"epoch_timestamp": start_timestamp + i * 60})

    # Add measurements from each meter
    for col_idx, column_name in enumerate(CHECKWATT_COLUMNS):
        measurements = json_data["Meters"][col_idx]["Measurements"]

        for i in range(len(measurements_soc)):
            if i < len(measurements):
                data_points[i][column_name] = measurements[i]["Value"]
            else:
                data_points[i][column_name] = 0.0

    # Remove all values from last record except Battery_SoC
    # (last record is incomplete delta)
    if len(data_points) > 0:
        for column_name in CHECKWATT_COLUMNS[1:]:  # Skip Battery_SoC
            data_points[-1].pop(column_name, None)

    logger.info(f"Processed {len(data_points)} CheckWatt data points")
    return data_points


async def write_checkwatt_to_influx(data_points: list[dict], dry_run: bool = False) -> bool:
    """
    Write CheckWatt data to InfluxDB.

    Args:
        data_points: Processed CheckWatt data points
        dry_run: If True, only log what would be written

    Returns:
        True if successful
    """
    config = get_config()

    if not data_points:
        logger.warning("No CheckWatt data to write")
        return False

    if dry_run:
        logger.info(f"[DRY-RUN] Would write {len(data_points)} CheckWatt data points")
        if len(data_points) > 0:
            first_ts = datetime.datetime.utcfromtimestamp(data_points[0]["epoch_timestamp"])
            last_ts = datetime.datetime.utcfromtimestamp(data_points[-1]["epoch_timestamp"])
            logger.info(f"[DRY-RUN]   From: {first_ts}")
            logger.info(f"[DRY-RUN]   To: {last_ts}")
            logger.info(f"[DRY-RUN]   First point fields: {list(data_points[0].keys())}")
        logger.info(f"[DRY-RUN] Bucket: {config.influxdb_bucket_checkwatt}")
        return True

    try:
        influx = InfluxClient(config)

        records = []
        for row in data_points:
            window_start_timestamp = row["epoch_timestamp"]
            window_start_datetime = datetime.datetime.utcfromtimestamp(window_start_timestamp)

            p = influxdb_client.Point("checkwatt")

            for key, value in row.items():
                if key != "epoch_timestamp":
                    p = p.field(key, value)

            p = p.time(window_start_datetime)
            records.append(p)

        # Write using influx client directly (not using wrapper for this one)
        influx.write_api.write(
            bucket=config.influxdb_bucket_checkwatt, org=config.influxdb_org, record=records
        )

        first_ts = datetime.datetime.utcfromtimestamp(data_points[0]["epoch_timestamp"])
        last_ts = datetime.datetime.utcfromtimestamp(data_points[-1]["epoch_timestamp"])

        logger.info(
            f"Wrote {len(records)} CheckWatt records to InfluxDB " f"(from {first_ts} to {last_ts})"
        )
        return True

    except Exception as e:
        logger.error(f"Exception writing CheckWatt data to InfluxDB: {e}")
        return False


async def collect_checkwatt_data(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    last_hour_only: bool = False,
    dry_run: bool = False,
) -> int:
    """
    Main function to collect CheckWatt data.

    Args:
        start_date: Start date (ISO format), defaults to today
        end_date: End date (ISO format), defaults to tomorrow
        last_hour_only: If True, only fetch last hour of data
        dry_run: If True, don't write to InfluxDB

    Returns:
        0 on success, 1 on failure
    """
    logger.info("Starting CheckWatt data collection")

    config = get_config()

    # Get credentials from config
    username = config.get("checkwatt_username")
    password = config.get("checkwatt_password")
    meter_ids_str = config.get("checkwatt_meter_ids")

    if not username or not password:
        logger.error("CHECKWATT_USERNAME and CHECKWATT_PASSWORD must be configured!")
        return 1

    if not meter_ids_str:
        logger.error("CHECKWATT_METER_IDS must be configured!")
        return 1

    meter_ids = [mid.strip() for mid in meter_ids_str.split(",")]

    # Determine date range
    if last_hour_only:
        now = datetime.datetime.now()
        start_date = (
            (now - datetime.timedelta(hours=1)).replace(minute=0, second=0).isoformat()[:19]
        )
        end_date = (now + datetime.timedelta(hours=1)).isoformat()[:19]
    else:
        if start_date is None:
            start_date = datetime.date.today().isoformat() + "T00:00:00"
        if end_date is None:
            end_date = (
                datetime.date.today() + datetime.timedelta(days=1)
            ).isoformat() + "T00:00:00"

    logger.info(f"Date range: {start_date} to {end_date}")

    # Get auth token
    auth_token = await get_auth_token(username, password)

    if not auth_token:
        logger.error("Failed to get auth token")
        return 1

    # Fetch data
    json_data = await fetch_checkwatt_data(auth_token, meter_ids, start_date, end_date)

    if not json_data:
        logger.error("Failed to fetch CheckWatt data")
        return 1

    # Log raw data to JSON for backup (with 1 week retention)
    json_logger = JSONDataLogger("checkwatt")
    json_logger.log_data(
        json_data,
        metadata={
            "start_date": start_date,
            "end_date": end_date,
            "meter_count": len(json_data.get("Meters", [])),
        },
    )
    json_logger.cleanup_old_logs()

    # Validate response format
    if len(json_data) != 4:
        logger.error(f"Unexpected response format (expected 4 fields, got {len(json_data)})")
        return 1

    # Process data
    try:
        data_points = process_checkwatt_data(json_data)
    except Exception as e:
        logger.error(f"Failed to process CheckWatt data: {e}")
        return 1

    if len(data_points) < 10:
        logger.error(f"Too little data ({len(data_points)} points). Check input.")
        return 1

    # Write to InfluxDB
    success = await write_checkwatt_to_influx(data_points, dry_run=dry_run)

    if success:
        logger.info("Successfully updated InfluxDB with CheckWatt data")
        return 0
    else:
        logger.error("Failed to write CheckWatt data to InfluxDB")
        return 1


def main():
    """Main entry point for CheckWatt collection."""
    import argparse

    parser = argparse.ArgumentParser(description="Collect CheckWatt battery and solar data")
    parser.add_argument(
        "--dry-run", action="store_true", help="Collect data but do not write to InfluxDB"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose debug logging")
    parser.add_argument(
        "--last-hour",
        action="store_true",
        help="Only fetch data from last hour (default mode for cron)",
    )
    parser.add_argument("--start-date", help="Start date (YYYY-MM-DDTHH:MM:SS format)")
    parser.add_argument("--end-date", help="End date (YYYY-MM-DDTHH:MM:SS format)")

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    if args.dry_run:
        logger.info("Starting CheckWatt collection (DRY-RUN mode)")
    else:
        logger.info("Starting CheckWatt collection")

    try:
        result = asyncio.run(
            collect_checkwatt_data(
                start_date=args.start_date,
                end_date=args.end_date,
                last_hour_only=args.last_hour,
                dry_run=args.dry_run,
            )
        )
        return result

    except Exception as e:
        logger.error(f"Unhandled exception in CheckWatt collection: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
