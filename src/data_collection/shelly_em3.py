#!/usr/bin/env python
"""Shelly EM3 energy meter data collection."""

import asyncio
import datetime
import os
from typing import Optional

import aiohttp
import pytz

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.json_logger import JSONDataLogger
from src.common.logger import setup_logger

logger = setup_logger(__name__, "shelly_em3.log")


async def fetch_shelly_em3_status(device_url: str) -> Optional[dict]:
    """
    Fetch status data from Shelly EM3 device.

    Args:
        device_url: Base URL of Shelly EM3 device (e.g., http://192.168.1.5)

    Returns:
        JSON status data, or None if failed
    """
    status_url = f"{device_url}/status"
    logger.info(f"Fetching Shelly EM3 status from {status_url}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(status_url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    json_data = await response.json()
                    logger.info("Successfully fetched Shelly EM3 status")
                    return json_data
                else:
                    logger.error(
                        f"Status request failed with status {response.status}: "
                        f"{await response.text()}"
                    )
                    return None

    except asyncio.TimeoutError:
        logger.error("Timeout fetching Shelly EM3 status")
        return None
    except Exception as e:
        logger.error(f"Exception fetching Shelly EM3 status: {e}")
        return None


def process_shelly_em3_data(status_data: dict) -> dict:
    """
    Process Shelly EM3 status data into point for InfluxDB.

    Args:
        status_data: Raw status JSON from Shelly EM3

    Returns:
        Dictionary with processed fields
    """
    if "emeters" not in status_data or len(status_data["emeters"]) != 3:
        raise ValueError(
            f"Invalid Shelly EM3 data: expected 3 emeters, got {len(status_data.get('emeters', []))}"
        )

    fields = {}

    # Process each of the three phases
    for phase_idx, emeter in enumerate(status_data["emeters"]):
        phase_num = phase_idx + 1

        # Instant measurements
        fields[f"phase{phase_num}_power"] = emeter.get("power", 0.0)
        fields[f"phase{phase_num}_current"] = emeter.get("current", 0.0)
        fields[f"phase{phase_num}_voltage"] = emeter.get("voltage", 0.0)
        fields[f"phase{phase_num}_pf"] = emeter.get("pf", 0.0)

        # Energy totals (Wh)
        fields[f"phase{phase_num}_total"] = emeter.get("total", 0.0)
        fields[f"phase{phase_num}_total_returned"] = emeter.get("total_returned", 0.0)

        # Net energy for this phase (consumed - returned)
        fields[f"phase{phase_num}_net_total"] = emeter.get("total", 0.0) - emeter.get(
            "total_returned", 0.0
        )

    # Sum across all three phases
    fields["total_power"] = sum(status_data["emeters"][i].get("power", 0.0) for i in range(3))

    fields["total_energy"] = sum(status_data["emeters"][i].get("total", 0.0) for i in range(3))

    fields["total_energy_returned"] = sum(
        status_data["emeters"][i].get("total_returned", 0.0) for i in range(3)
    )

    # Net total energy across all phases (consumed - returned)
    fields["net_total_energy"] = fields["total_energy"] - fields["total_energy_returned"]

    logger.info(
        f"Processed Shelly EM3 data: total_power={fields['total_power']:.1f}W, "
        f"net_total_energy={fields['net_total_energy']:.1f}Wh"
    )

    return fields


async def write_shelly_em3_to_influx(fields: dict, dry_run: bool = False) -> bool:
    """
    Write Shelly EM3 data point to InfluxDB.

    Args:
        fields: Processed fields dictionary
        dry_run: If True, don't actually write to database

    Returns:
        True if successful, False otherwise
    """
    if dry_run:
        logger.info(f"DRY RUN: Would write Shelly EM3 data: {fields}")
        return True

    try:
        config = get_config()
        influx_client = InfluxClient(config)

        # Use current time for measurement
        timestamp = datetime.datetime.now(pytz.UTC)

        # Write to shelly_em3_emeter_raw bucket
        bucket = config.influxdb_bucket_shelly_em3_raw
        influx_client.write_point(
            bucket=bucket, measurement="shelly_em3", fields=fields, timestamp=timestamp
        )

        logger.info(f"Wrote Shelly EM3 data to InfluxDB bucket '{bucket}'")
        return True

    except Exception as e:
        logger.error(f"Failed to write Shelly EM3 data to InfluxDB: {e}")
        return False


async def collect_shelly_em3_data(dry_run: bool = False) -> int:
    """
    Main collection function for Shelly EM3 data.

    Args:
        dry_run: If True, don't write to database

    Returns:
        0 on success, 1 on failure
    """
    logger.info("Starting Shelly EM3 data collection")

    # Get device URL from environment (required)
    device_url = os.getenv("SHELLY_EM3_URL")
    if not device_url:
        logger.error("SHELLY_EM3_URL environment variable not set")
        return 1

    # Fetch status data
    status_data = await fetch_shelly_em3_status(device_url)

    if status_data is None:
        logger.error("Failed to fetch Shelly EM3 status")
        return 1

    # Log raw data for debugging
    json_logger = JSONDataLogger("shelly_em3")
    json_logger.log_data(status_data, metadata={"device_url": device_url})
    json_logger.cleanup_old_logs()

    # Process data
    try:
        fields = process_shelly_em3_data(status_data)
    except Exception as e:
        logger.error(f"Failed to process Shelly EM3 data: {e}")
        return 1

    # Write to InfluxDB
    success = await write_shelly_em3_to_influx(fields, dry_run=dry_run)

    if success:
        logger.info("Shelly EM3 data collection completed successfully")
        return 0
    else:
        logger.error("Shelly EM3 data collection failed")
        return 1


def main():
    """Main entry point for Shelly EM3 data collection."""
    import argparse

    parser = argparse.ArgumentParser(description="Collect Shelly EM3 energy meter data")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write to database, just log what would be done",
    )

    args = parser.parse_args()

    exit_code = asyncio.run(collect_shelly_em3_data(dry_run=args.dry_run))
    return exit_code


if __name__ == "__main__":
    import sys

    sys.exit(main())
