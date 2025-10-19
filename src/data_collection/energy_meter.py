#!/usr/bin/env python
"""Energy meter data collection from Shelly EM3."""

import asyncio
import datetime
from typing import Optional

import aiohttp
import influxdb_client

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.json_logger import JSONDataLogger
from src.common.logger import setup_logger

logger = setup_logger(__name__, "energy_meter.log")


async def fetch_shelly_em3_data(shelly_url: str) -> Optional[dict]:
    """
    Fetch energy meter data from Shelly EM3.

    Args:
        shelly_url: Shelly EM3 base URL (e.g., http://192.168.1.5)

    Returns:
        Dict with energy meter data, or None if failed
    """
    status_url = f"{shelly_url}/status"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(status_url) as response:
                if response.status == 200:
                    json_data = await response.json()

                    # Sum all three phases
                    total_consumed = 0.0
                    total_returned = 0.0

                    for emeter in json_data.get("emeters", []):
                        total_consumed += emeter.get("total", 0.0)
                        total_returned += emeter.get("total_returned", 0.0)

                    net_energy = total_consumed - total_returned

                    logger.info(
                        f"Shelly EM3: consumed={total_consumed:.1f} kWh, "
                        f"returned={total_returned:.1f} kWh, "
                        f"net={net_energy:.1f} kWh"
                    )

                    return {
                        "total_consumed": round(total_consumed, 1),
                        "total_returned": round(total_returned, 1),
                        "net_energy": round(net_energy, 1),
                        "raw_data": json_data,
                    }
                else:
                    logger.error(f"Shelly EM3 request failed with status {response.status}")
                    return None

    except Exception as e:
        logger.error(f"Exception fetching Shelly EM3 data: {e}")
        return None


def calculate_energy_metrics(
    current_data: dict, previous_data: Optional[dict], time_diff_seconds: float
) -> dict:
    """
    Calculate energy consumption metrics.

    Args:
        current_data: Current energy meter data
        previous_data: Previous measurement data
        time_diff_seconds: Time difference between measurements

    Returns:
        Dict with calculated metrics
    """
    metrics = {
        "timestamp": current_data["timestamp"],
        "emeter_net": current_data["net_energy"],
        "emeter_consumed": current_data["total_consumed"],
        "emeter_returned": current_data["total_returned"],
    }

    if previous_data and time_diff_seconds > 0:
        # Calculate differences (delta energy in kWh)
        net_diff = current_data["net_energy"] - previous_data["net_energy"]
        consumed_diff = current_data["total_consumed"] - previous_data["total_consumed"]
        returned_diff = current_data["total_returned"] - previous_data["total_returned"]

        # Calculate averages (power in W)
        time_diff_hours = time_diff_seconds / 3600.0

        metrics["emeter_net_avg_w"] = (net_diff / time_diff_hours) * 1000.0
        metrics["emeter_consumed_avg_w"] = (consumed_diff / time_diff_hours) * 1000.0
        metrics["emeter_returned_avg_w"] = (returned_diff / time_diff_hours) * 1000.0

        metrics["emeter_net_diff_kwh"] = net_diff
        metrics["emeter_consumed_diff_kwh"] = consumed_diff
        metrics["emeter_returned_diff_kwh"] = returned_diff
        metrics["time_diff_seconds"] = time_diff_seconds

        logger.info(
            f"Metrics: net={metrics['emeter_net_avg_w']:.0f}W, "
            f"consumed={metrics['emeter_consumed_avg_w']:.0f}W, "
            f"returned={metrics['emeter_returned_avg_w']:.0f}W "
            f"(over {time_diff_seconds:.0f}s)"
        )

    return metrics


async def write_energy_to_influx(metrics: dict, dry_run: bool = False) -> bool:
    """
    Write energy metrics to InfluxDB.

    Args:
        metrics: Energy metrics to write
        dry_run: If True, only log what would be written

    Returns:
        True if successful
    """
    config = get_config()

    if dry_run:
        logger.info("[DRY-RUN] Would write energy metrics:")
        for key, value in metrics.items():
            if key != "timestamp":
                logger.info(f"[DRY-RUN]   {key}: {value}")
        logger.info(f"[DRY-RUN] Bucket: {config.influxdb_bucket_energy}")
        return True

    try:
        influx = InfluxClient(config)

        timestamp = datetime.datetime.fromisoformat(metrics["timestamp"])

        p = influxdb_client.Point("energy")

        # Add all metrics as fields
        for key, value in metrics.items():
            if key != "timestamp" and value is not None:
                p = p.field(key, value)

        p = p.time(timestamp)

        # Write to InfluxDB
        influx.write_api.write(
            bucket=config.influxdb_bucket_energy,
            org=config.influxdb_org,
            record=p,
        )

        logger.info(f"Wrote energy metrics to InfluxDB at {timestamp}")
        return True

    except Exception as e:
        logger.error(f"Exception writing energy data to InfluxDB: {e}")
        return False


async def collect_energy_data(
    previous_data: Optional[dict] = None, dry_run: bool = False
) -> tuple[int, Optional[dict]]:
    """
    Main function to collect energy meter data.

    Args:
        previous_data: Previous measurement for calculating deltas
        dry_run: If True, don't write to InfluxDB

    Returns:
        Tuple of (exit_code, current_data_dict)
    """
    logger.info("Starting energy meter data collection")

    config = get_config()

    # Get Shelly EM3 URL from config
    shelly_url = config.get("shelly_em3_url")

    if not shelly_url:
        logger.error("SHELLY_EM3_URL must be configured!")
        return 1, None

    # Fetch Shelly EM3 data
    shelly_data = await fetch_shelly_em3_data(shelly_url)

    if not shelly_data:
        logger.error("Failed to fetch Shelly EM3 data")
        return 1, None

    # Build current data dict
    current_timestamp = datetime.datetime.now()
    current_data = {
        "timestamp": current_timestamp.isoformat(),
        "epoch_timestamp": int(current_timestamp.timestamp()),
        "net_energy": shelly_data["net_energy"],
        "total_consumed": shelly_data["total_consumed"],
        "total_returned": shelly_data["total_returned"],
    }

    # Log raw data to JSON for backup (30 days retention for local sensor data)
    json_logger = JSONDataLogger("energy_meter")
    json_logger.retention_days = 30
    json_logger.log_data(
        shelly_data,
        metadata={
            "timestamp": current_data["timestamp"],
            "net_energy": current_data["net_energy"],
        },
    )
    json_logger.cleanup_old_logs()

    # Calculate metrics (need previous data)
    if previous_data:
        time_diff = current_data["epoch_timestamp"] - previous_data["epoch_timestamp"]
        metrics = calculate_energy_metrics(current_data, previous_data, time_diff)
    else:
        logger.warning("No previous data available, skipping delta calculations")
        metrics = {
            "timestamp": current_data["timestamp"],
            "emeter_net": current_data["net_energy"],
            "emeter_consumed": current_data["total_consumed"],
            "emeter_returned": current_data["total_returned"],
        }

    # Write to InfluxDB
    success = await write_energy_to_influx(metrics, dry_run=dry_run)

    if success:
        logger.info("Successfully completed energy meter data collection")
        return 0, current_data
    else:
        logger.error("Failed to write energy meter data")
        return 1, current_data


def main():
    """Main entry point for energy meter collection."""
    import argparse
    import json
    import os

    parser = argparse.ArgumentParser(description="Collect energy meter data from Shelly EM3")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect data but do not write to InfluxDB",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose debug logging",
    )
    parser.add_argument(
        "--status-file",
        default="energy_meter_status.json",
        help="Status file to track previous measurement",
    )

    args = parser.parse_args()

    if args.verbose:
        import logging

        logger.setLevel(logging.DEBUG)

    # Load previous measurement data
    previous_data = None
    if os.path.exists(args.status_file):
        try:
            with open(args.status_file) as f:
                previous_data = json.load(f)
            logger.info(f"Loaded previous measurement from {args.status_file}")
        except Exception as e:
            logger.warning(f"Could not load status file: {e}")

    if args.dry_run:
        logger.info("Starting energy meter collection (DRY-RUN mode)")
    else:
        logger.info("Starting energy meter collection")

    try:
        exit_code, current_data = asyncio.run(
            collect_energy_data(previous_data=previous_data, dry_run=args.dry_run)
        )

        # Save current data as previous for next run
        if exit_code == 0 and current_data and not args.dry_run:
            try:
                with open(args.status_file, "w") as f:
                    json.dump(current_data, f)
                logger.info(f"Saved current data to {args.status_file}")
            except Exception as e:
                logger.error(f"Failed to save status file: {e}")

        return exit_code

    except Exception as e:
        logger.error(f"Unhandled exception in energy meter collection: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
