#!/usr/bin/env python
"""Wind power production and forecast data collection from Fingrid and FMI."""

import asyncio
import datetime
import time
from typing import Any, Optional, Union

import aiohttp
import influxdb_client

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.json_logger import JSONDataLogger
from src.common.logger import setup_logger

logger = setup_logger(__name__, "windpower.log")

# Fingrid API configuration
FINGRID_API_URL = "https://data.fingrid.fi/api/datasets/{variable_id}/data"
FINGRID_API_KEY = "779865ac3644488cb77186b98df787cb"

# FMI wind power forecast URL
FMI_WINDPOWER_URL = (
    "https://cdn.fmi.fi/products/renewable-energy-forecasts/wind/windpower_fi_latest.json"
)

# Variable ID to field name mapping (Fingrid)
FINGRID_VARIABLES = {
    245: "Hourly forecast",
    246: "Daily forecast",
    75: "Production",
    268: "Max capacity",
}

DATEFORMAT_DATA = "%Y-%m-%dT%H:%M:%S.000Z"
DATEFORMAT_QUERY = "%Y-%m-%dT%H:%M:%SZ"


async def fetch_fingrid_data(
    variable_id: int, start_time_utc: datetime.datetime, end_time_utc: datetime.datetime
) -> Optional[list[dict]]:
    """
    Fetch wind power data from Fingrid API.

    Args:
        variable_id: Fingrid variable ID
        start_time_utc: Start time (UTC)
        end_time_utc: End time (UTC)

    Returns:
        List of data points, or None if failed
    """
    url = FINGRID_API_URL.format(variable_id=variable_id)
    url += f"?startTime={start_time_utc.strftime(DATEFORMAT_QUERY)}"
    url += f"&endTime={end_time_utc.strftime(DATEFORMAT_QUERY)}"
    url += "&pageSize=20000"

    headers = {"x-api-key": FINGRID_API_KEY, "Accept": "application/json"}

    tries_left = 10
    status = 0

    while tries_left > 0 and status != 200:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    status = response.status

                    if status == 200:
                        response_json = await response.json()

                        # Handle both old and new API response formats
                        if isinstance(response_json, dict) and "data" in response_json:
                            data = response_json["data"]
                        else:
                            data = response_json

                        logger.info(
                            f"Fetched {len(data)} records for variable {variable_id} "
                            f"({FINGRID_VARIABLES.get(variable_id, 'Unknown')})"
                        )
                        return data

                    elif status == 429:
                        logger.warning("Rate limited by Fingrid API, sleeping 2.5s")
                        time.sleep(2.5)
                        tries_left -= 1
                    else:
                        response_text = await response.text()
                        logger.error(f"Fingrid API error {status}: {response_text}")
                        tries_left -= 1

        except Exception as e:
            logger.error(f"Exception fetching Fingrid data: {e}")
            tries_left -= 1
            if tries_left > 0:
                time.sleep(1)

    logger.error(f"Failed to fetch variable {variable_id} after retries")
    return None


async def fetch_fmi_windpower_forecast() -> Optional[dict]:
    """
    Fetch wind power forecast from FMI.

    Returns:
        FMI forecast data, or None if failed
    """
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,fi;q=0.8",
        "Origin": "https://www.ilmatieteenlaitos.fi",
        "Referer": "https://www.ilmatieteenlaitos.fi/",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(FMI_WINDPOWER_URL, headers=headers) as response:
                if response.status == 200:
                    response_json = await response.json()
                    logger.info("Successfully fetched FMI wind power forecast")
                    return response_json
                else:
                    response_text = await response.text()
                    logger.error(f"FMI API error {response.status}: {response_text}")
                    return None

    except Exception as e:
        logger.error(f"Exception fetching FMI forecast: {e}")
        return None


async def fetch_all_windpower_data(
    start_time_local: datetime.datetime, end_time_local: datetime.datetime
) -> dict:
    """
    Fetch all wind power data (Fingrid + FMI).

    Args:
        start_time_local: Start time (local timezone)
        end_time_local: End time (local timezone)

    Returns:
        Dict with field names as keys and data as values
    """
    logger.info(f"Fetching wind power data from {start_time_local} to {end_time_local}")

    # Convert to UTC
    start_time_utc = datetime.datetime.utcfromtimestamp(start_time_local.timestamp())
    end_time_utc = datetime.datetime.utcfromtimestamp(end_time_local.timestamp())

    responses: dict[str, Any] = {}

    # Fetch Fingrid data for all variables
    for variable_id, field_name in FINGRID_VARIABLES.items():
        data = await fetch_fingrid_data(variable_id, start_time_utc, end_time_utc)
        if data:
            responses[field_name] = data

    # Fetch FMI forecast
    fmi_data = await fetch_fmi_windpower_forecast()
    if fmi_data:
        responses["FMI forecast"] = fmi_data

    return responses


def process_windpower_data(responses: dict) -> dict[datetime.datetime, dict]:
    """
    Process raw wind power data into time-indexed format.

    Args:
        responses: Raw responses from APIs

    Returns:
        Dict mapping timestamps to field values
    """
    logger.info("Processing wind power data")
    processed_rows: dict[Any, Any] = {}

    for field, data in responses.items():
        if field == "FMI forecast":
            # Process FMI forecast data
            try:
                # Validate timezone
                if data.get("time", {}).get("timezone") != "Europe/Helsinki":
                    logger.warning(
                        f"FMI timezone is {data.get('time', {}).get('timezone')}, "
                        "expected Europe/Helsinki"
                    )

                # Extract data points
                for row in data["series"][0]["data"]:
                    timestamp_ms = int(row[0])
                    value_kw = row[1]

                    # Convert to UTC datetime and MW
                    timestamp = datetime.datetime.utcfromtimestamp(timestamp_ms / 1000)
                    value_mw = value_kw * 1000.0

                    if timestamp not in processed_rows:
                        processed_rows[timestamp] = {}
                    processed_rows[timestamp][field] = value_mw

            except Exception as e:
                logger.error(f"Error processing FMI forecast: {e}")

        else:
            # Process Fingrid data
            for row in data:
                try:
                    # Parse timestamp
                    timestamp = datetime.datetime.utcfromtimestamp(
                        datetime.datetime.strptime(row["startTime"], DATEFORMAT_DATA)
                        .replace(tzinfo=datetime.timezone.utc)
                        .timestamp()
                    )

                    # Convert value based on field type
                    value: Union[int, float, Any]
                    if field in ["Production", "Max capacity"]:
                        value = int(round(row["value"]))
                    elif field in ["Hourly forecast", "Daily forecast"]:
                        value = float(row["value"])
                    else:
                        value = row["value"]

                    if timestamp not in processed_rows:
                        processed_rows[timestamp] = {}
                    processed_rows[timestamp][field] = value

                except Exception as e:
                    logger.error(f"Error processing Fingrid data row: {e}")
                    continue

    logger.info(f"Processed {len(processed_rows)} time points")
    return processed_rows


async def write_windpower_to_influx(
    processed_data: dict[datetime.datetime, dict], dry_run: bool = False
) -> Optional[datetime.datetime]:
    """
    Write processed wind power data to InfluxDB.

    Args:
        processed_data: Processed wind power data
        dry_run: If True, only log what would be written

    Returns:
        Latest timestamp written, or None if failed
    """
    config = get_config()

    if not processed_data:
        logger.warning("No wind power data to write")
        return None

    if dry_run:
        logger.info(f"[DRY-RUN] Would write {len(processed_data)} wind power entries")
        if processed_data:
            timestamps = sorted(processed_data.keys())
            first_ts = timestamps[0]
            last_ts = timestamps[-1]
            logger.info(f"[DRY-RUN]   From: {first_ts}")
            logger.info(f"[DRY-RUN]   To: {last_ts}")
            logger.info(f"[DRY-RUN]   Fields: {list(processed_data[first_ts].keys())}")
        logger.info(f"[DRY-RUN] Bucket: {config.influxdb_bucket_windpower}")
        return max(processed_data.keys())

    try:
        influx = InfluxClient(config)

        points = []
        for timestamp, entry in processed_data.items():
            try:
                p = influxdb_client.Point("windpower")

                # Add all fields
                for field_name, value in entry.items():
                    p = p.field(field_name, value)

                p = p.time(timestamp)
                points.append(p)

            except Exception as e:
                logger.error(f"Error creating point for {timestamp}: {e}")
                continue

        if not points:
            logger.error("No valid points to write")
            return None

        # Write to InfluxDB
        influx.write_api.write(
            bucket=config.influxdb_bucket_windpower, org=config.influxdb_org, record=points
        )

        latest_timestamp = max(processed_data.keys())
        earliest_timestamp = min(processed_data.keys())

        logger.info(
            f"Wrote {len(points)} wind power points to InfluxDB "
            f"(from {earliest_timestamp} to {latest_timestamp})"
        )
        return latest_timestamp

    except Exception as e:
        logger.error(f"Exception writing wind power data to InfluxDB: {e}")
        return None


async def collect_windpower_data(
    start_time: Optional[datetime.datetime] = None,
    end_time: Optional[datetime.datetime] = None,
    dry_run: bool = False,
) -> int:
    """
    Main function to collect wind power data.

    Args:
        start_time: Start time (local), defaults to 2 days ago
        end_time: End time (local), defaults to 3 days from now
        dry_run: If True, don't write to InfluxDB

    Returns:
        0 on success, 1 on failure
    """
    logger.info("Starting wind power data collection")

    # Determine time range
    if start_time is None:
        start_time = datetime.datetime.now() - datetime.timedelta(days=2)
    if end_time is None:
        end_time = datetime.datetime.now() + datetime.timedelta(days=3)

    # Fetch raw data from APIs
    windpower_raw = await fetch_all_windpower_data(start_time, end_time)

    if not windpower_raw:
        logger.error("Failed to fetch wind power data from any source")
        return 1

    # Log raw data to JSON for backup
    json_logger = JSONDataLogger("windpower")
    json_logger.log_data(
        windpower_raw,
        metadata={
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "sources": list(windpower_raw.keys()),
        },
    )
    json_logger.cleanup_old_logs()

    # Process data
    processed_data = process_windpower_data(windpower_raw)

    if not processed_data:
        logger.error("No data after processing")
        return 1

    # Write to InfluxDB
    latest_timestamp = await write_windpower_to_influx(processed_data, dry_run=dry_run)

    if latest_timestamp is None:
        logger.error("Failed to write wind power data")
        return 1

    logger.info("Successfully completed wind power data collection")
    return 0


def main():
    """Main entry point for wind power collection."""
    import argparse

    parser = argparse.ArgumentParser(description="Collect wind power production and forecasts")
    parser.add_argument(
        "--dry-run", action="store_true", help="Collect data but do not write to InfluxDB"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose debug logging")
    parser.add_argument("--start-date", help="Start date (YYYY-MM-DD format)")
    parser.add_argument("--end-date", help="End date (YYYY-MM-DD format)")

    args = parser.parse_args()

    if args.verbose:
        import logging

        logger.setLevel(logging.DEBUG)

    # Parse dates if provided
    start_time = None
    end_time = None

    if args.start_date:
        start_time = datetime.datetime.fromisoformat(args.start_date)
    if args.end_date:
        end_time = datetime.datetime.fromisoformat(args.end_date)

    if args.dry_run:
        logger.info("Starting wind power collection (DRY-RUN mode)")
    else:
        logger.info("Starting wind power collection")

    try:
        result = asyncio.run(
            collect_windpower_data(start_time=start_time, end_time=end_time, dry_run=args.dry_run)
        )
        return result

    except Exception as e:
        logger.error(f"Unhandled exception in wind power collection: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
