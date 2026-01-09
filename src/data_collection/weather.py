#!/usr/bin/env python
"""Weather forecast data collection from FMI (Finnish Meteorological Institute)."""

import datetime
import json
import os
from typing import Any, Optional

from fmiopendata.wfs import download_stored_query

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.json_logger import JSONDataLogger
from src.common.logger import setup_logger

logger = setup_logger(__name__, "weather.log")

# FMI query parameters
FMI_QUERY = "fmi::forecast::harmonie::surface::point::multipointcoverage"
FMI_TIMESTEP = "15"  # 15 minute intervals

# Fields to exclude from storage
EXCLUDED_FIELDS = ["Geopotential height"]


def fetch_weather_forecast(latlon: str) -> dict[datetime.datetime, dict[str, float]]:
    """
    Fetch weather forecast from FMI API.

    Args:
        latlon: Latitude and longitude as "lat,lon" string

    Returns:
        Dictionary mapping timestamps to weather data fields
    """
    logger.info(f"Requesting weather forecast for latlon={latlon}")

    try:
        weather_data = download_stored_query(
            FMI_QUERY, [f"latlon={latlon}", f"timestep={FMI_TIMESTEP}"]
        )

        if not weather_data or not weather_data.data:
            logger.error("No weather data received from FMI")
            return {}

        valid_times = list(weather_data.data.keys())

        if not valid_times:
            logger.error("No valid times in weather data")
            return {}

        earliest_time = min(valid_times)
        latest_time = max(valid_times)

        # Get the level (usually "0" for surface data)
        level = list(weather_data.data[valid_times[0]].keys())[0]

        logger.info(
            f"Downloaded weather for level {level} from "
            f"{earliest_time.strftime('%Y-%m-%d %H:%M')}Z to "
            f"{latest_time.strftime('%Y-%m-%d %H:%M')}Z"
        )

        # Process data
        processed_data: dict[Any, Any] = {}

        for timestamp in valid_times:
            processed_data[timestamp] = {}

            datasets = weather_data.data[timestamp][level]
            for field_name, field_data in datasets.items():
                # Skip excluded fields
                if field_name in EXCLUDED_FIELDS:
                    continue

                # Extract value
                if isinstance(field_data, dict) and "value" in field_data:
                    processed_data[timestamp][field_name] = field_data["value"]
                else:
                    logger.warning(f"Unexpected data format for field {field_name}: {field_data}")

        logger.info(f"Processed {len(processed_data)} weather forecast timestamps")
        return processed_data

    except Exception as e:
        logger.error(f"Exception fetching weather forecast: {e}")
        return {}


def save_weather_to_file(
    weather_data: dict[datetime.datetime, dict[str, float]],
    base_dir: str = "/var/log/home-automation/weather_data",
) -> Optional[str]:
    """
    Save weather data to JSON file for backup/debugging.

    Args:
        weather_data: Weather forecast data
        base_dir: Base directory for weather data files

    Returns:
        Path to saved file, or None if failed
    """
    try:
        now = datetime.datetime.utcnow()

        # Create year-based subdirectory
        year_dir = os.path.join(base_dir, now.strftime("%Y"))
        os.makedirs(year_dir, exist_ok=True)

        # Generate filename
        filename = now.strftime("weather_data_%Y-%m-%dT%H-%MZ.json")
        filepath = os.path.join(year_dir, filename)

        # Convert datetime keys to epoch timestamps for JSON serialization
        json_data = {}
        for timestamp, fields in weather_data.items():
            epoch = int(timestamp.timestamp())
            json_data[epoch] = fields

        # Write to file
        with open(filepath, "w") as f:
            json.dump(json_data, f, indent=2)

        logger.info(f"Saved weather data to {filepath}")
        return filepath

    except Exception as e:
        logger.error(f"Failed to save weather data to file: {e}")
        return None


def write_weather_to_influx(
    weather_data: dict[datetime.datetime, dict[str, float]], dry_run: bool = False
) -> bool:
    """
    Write weather forecast data to InfluxDB.

    Args:
        weather_data: Weather forecast data
        dry_run: If True, only log what would be written

    Returns:
        True if successful, False otherwise
    """
    config = get_config()

    if not weather_data:
        logger.warning("No weather data to write")
        return False

    if dry_run:
        logger.info(f"[DRY-RUN] Would write {len(weather_data)} weather forecast points")
        for timestamp in sorted(weather_data.keys())[:3]:  # Show first 3
            fields = weather_data[timestamp]
            logger.info(f"[DRY-RUN]   {timestamp}: {len(fields)} fields")
            for field_name, value in list(fields.items())[:3]:  # Show first 3 fields
                logger.info(f"[DRY-RUN]     {field_name}: {value}")
        if len(weather_data) > 3:
            logger.info(f"[DRY-RUN]   ... and {len(weather_data) - 3} more timestamps")
        logger.info(f"[DRY-RUN] Bucket: {config.influxdb_bucket_weather}")
        return True

    try:
        influx = InfluxClient(config)

        # Use the weather-specific write method
        success = influx.write_weather(weather_data)

        if success:
            earliest = min(weather_data.keys())
            latest = max(weather_data.keys())
            logger.info(
                f"Wrote {len(weather_data)} weather forecast points to InfluxDB "
                f"(from {earliest} to {latest})"
            )
        else:
            logger.error("Failed to write weather data to InfluxDB")

        return success

    except Exception as e:
        logger.error(f"Exception writing weather to InfluxDB: {e}")
        return False


def collect_weather() -> dict[datetime.datetime, dict[str, float]]:
    """
    Main function to collect weather forecast data.

    Returns:
        Weather forecast data
    """
    config = get_config()

    # Get lat/lon from config (must be configured in .env)
    latlon = config.get("weather_latlon")

    if not latlon:
        logger.error("WEATHER_LATLON not configured in .env file!")
        return {}

    logger.info("Starting weather forecast collection")

    # Fetch forecast
    weather_data = fetch_weather_forecast(latlon)

    if not weather_data:
        logger.error("No weather data collected")
        return {}

    # Log raw data to JSON for backup (with 1 week retention)
    json_logger = JSONDataLogger("weather")
    # Convert datetime keys to strings for JSON serialization
    weather_data_serializable = {
        timestamp.isoformat(): fields for timestamp, fields in weather_data.items()
    }
    json_logger.log_data(
        weather_data_serializable,
        metadata={"latlon": latlon, "num_timestamps": len(weather_data)},
    )
    json_logger.cleanup_old_logs()

    logger.info(f"Successfully collected {len(weather_data)} forecast timestamps")
    return weather_data


def main():
    """Main entry point for weather collection."""
    import argparse

    parser = argparse.ArgumentParser(description="Collect weather forecast from FMI")
    parser.add_argument(
        "--dry-run", action="store_true", help="Collect data but do not write to InfluxDB"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose debug logging")
    parser.add_argument(
        "--save-file", action="store_true", help="Save weather data to JSON file for backup"
    )

    args = parser.parse_args()

    if args.verbose:
        import logging

        logger.setLevel(logging.DEBUG)

    if args.dry_run:
        logger.info("Starting weather collection (DRY-RUN mode)")
    else:
        logger.info("Starting weather collection")

    try:
        # Collect weather data
        weather_data = collect_weather()

        if not weather_data:
            logger.warning("No weather data collected")
            return 1

        # Optionally save to file
        if args.save_file:
            save_weather_to_file(weather_data)

        # Write to InfluxDB
        success = write_weather_to_influx(weather_data, dry_run=args.dry_run)

        if success:
            if args.dry_run:
                logger.info("Weather collection DRY-RUN completed successfully")
            else:
                logger.info("Weather collection completed successfully")
            return 0
        else:
            logger.error("Weather collection failed")
            return 1

    except Exception as e:
        logger.error(f"Unhandled exception in weather collection: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
