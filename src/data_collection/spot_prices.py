#!/usr/bin/env python
"""Electricity spot price data collection from spot-hinta.fi API."""

import asyncio
import datetime
import json
import os
from typing import Optional

import aiohttp
import pytz

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.json_logger import JSONDataLogger
from src.common.logger import setup_logger

logger = setup_logger(__name__, "spot_prices.log")

# API endpoint
SPOT_PRICE_API_URL = "https://api.spot-hinta.fi/TodayAndDayForward"

# Status file to track latest uploaded price
STATUS_FILE = "spot_price_getter_status.json"


async def fetch_spot_prices_from_api() -> Optional[list[dict]]:
    """
    Fetch spot prices from spot-hinta.fi API.

    Returns:
        List of price entries, or None if failed
    """
    logger.info(f"Fetching spot prices from {SPOT_PRICE_API_URL}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(SPOT_PRICE_API_URL) as response:
                if response.status == 200:
                    response_text = await response.text()
                    response_json = json.loads(response_text)
                    logger.info(f"Successfully fetched {len(response_json)} price entries")
                    return response_json
                else:
                    logger.error(
                        f"API request failed with status {response.status}: "
                        f"{await response.text()}"
                    )
                    return None

    except Exception as e:
        logger.error(f"Exception fetching spot prices: {e}")
        return None


def process_spot_prices(spot_prices_raw: list[dict], config) -> list[dict]:
    """
    Process raw spot price data and calculate final prices.

    Args:
        spot_prices_raw: Raw price data from API
        config: Configuration object with price parameters

    Returns:
        List of processed price entries with all calculations
    """
    logger.info("Processing spot prices")

    # Get pricing parameters from config (all required!)
    required_params = [
        "spot_value_added_tax",
        "spot_sellers_margin",
        "spot_production_buyback_margin",
        "spot_transfer_day_price",
        "spot_transfer_night_price",
        "spot_transfer_tax_price",
    ]

    for param in required_params:
        if not config.get(param):
            logger.error(f"Required configuration parameter {param.upper()} not set!")
            raise ValueError(f"Missing required config: {param.upper()}")

    value_added_tax = float(config.get("spot_value_added_tax"))
    sellers_margin = float(config.get("spot_sellers_margin"))
    production_buyback_margin = float(config.get("spot_production_buyback_margin"))
    transfer_day_price = float(config.get("spot_transfer_day_price"))
    transfer_night_price = float(config.get("spot_transfer_night_price"))
    transfer_tax_price = float(config.get("spot_transfer_tax_price"))

    processed_spot_prices = []

    for hour_entry in spot_prices_raw:
        try:
            data = {}

            # Parse datetime
            entry_datetime = datetime.datetime.fromisoformat(hour_entry["DateTime"])

            # Handle DST transition (2022-10-30 specific fix from original code)
            if entry_datetime.isoformat() == "2022-10-30T03:00:00+02:00":
                offset = 3600
            else:
                offset = 0

            # Calculate epoch timestamp
            data["epoch_timestamp"] = int(entry_datetime.timestamp()) + offset

            # Store datetime in various formats
            data["datetime_utc"] = (
                datetime.datetime.utcfromtimestamp(data["epoch_timestamp"])
                .replace(tzinfo=pytz.utc)
                .isoformat()
            )
            data["datetime_local"] = entry_datetime.isoformat()

            # Price without tax (c/kWh)
            data["price"] = hour_entry["PriceNoTax"]

            # Selling price (production buyback)
            data["price_sell"] = round(
                hour_entry["PriceNoTax"] - 0.01 * production_buyback_margin, 4
            )

            # Price with VAT
            price_with_tax = round(value_added_tax * hour_entry["PriceNoTax"], 4)
            data["price_withtax"] = price_with_tax

            # Determine transfer price (night vs day rate)
            if entry_datetime.hour >= 22 or entry_datetime.hour < 7:
                transfer_price = transfer_night_price
            else:
                transfer_price = transfer_day_price

            # Total price including all fees and taxes
            data["price_total"] = round(
                price_with_tax + 0.01 * (sellers_margin + transfer_price + transfer_tax_price), 6
            )

            processed_spot_prices.append(data)

        except Exception as e:
            logger.error(f"Error processing entry {hour_entry}: {e}")
            continue

    logger.info(f"Processed {len(processed_spot_prices)} spot price entries")
    return processed_spot_prices


def save_spot_prices_to_file(
    spot_prices_raw: list[dict], filename: str = "spot_prices_cache.json"
) -> bool:
    """
    Save raw spot price data to JSON file for backup.

    Args:
        spot_prices_raw: Raw price data from API
        filename: Filename to save to

    Returns:
        True if successful
    """
    try:
        with open(filename, "w") as f:
            json.dump(spot_prices_raw, f, indent=2)
        logger.info(f"Saved spot prices to {filename}")
        return True
    except Exception as e:
        logger.error(f"Failed to save spot prices to file: {e}")
        return False


async def write_spot_prices_to_influx(
    processed_spot_prices: list[dict], dry_run: bool = False
) -> Optional[int]:
    """
    Write processed spot prices to InfluxDB.

    Args:
        processed_spot_prices: Processed price data
        dry_run: If True, only log what would be written

    Returns:
        Epoch timestamp of latest uploaded price, or None if failed
    """
    config = get_config()

    if not processed_spot_prices:
        logger.warning("No spot prices to write")
        return None

    if dry_run:
        logger.info(f"[DRY-RUN] Would write {len(processed_spot_prices)} spot price entries")
        for entry in processed_spot_prices[:3]:  # Show first 3
            logger.info(
                f"[DRY-RUN]   {entry['datetime_utc']}: " f"{entry['price_total']:.4f} c/kWh"
            )
        if len(processed_spot_prices) > 3:
            logger.info(f"[DRY-RUN]   ... and {len(processed_spot_prices) - 3} more")
        logger.info(f"[DRY-RUN] Bucket: {config.influxdb_bucket_spotprice}")

        # Return latest timestamp for dry-run
        latest = max(entry["epoch_timestamp"] for entry in processed_spot_prices)
        return latest

    try:
        # Use synchronous InfluxClient (simpler than async for our use case)
        influx = InfluxClient(config)

        # Write using spot price specific method
        success = influx.write_spot_prices(processed_spot_prices)

        if success:
            latest_timestamp = max(entry["epoch_timestamp"] for entry in processed_spot_prices)
            earliest_dt = datetime.datetime.utcfromtimestamp(
                min(entry["epoch_timestamp"] for entry in processed_spot_prices)
            )
            latest_dt = datetime.datetime.utcfromtimestamp(latest_timestamp)

            logger.info(
                f"Wrote {len(processed_spot_prices)} spot prices to InfluxDB "
                f"(from {earliest_dt} to {latest_dt})"
            )
            return latest_timestamp
        else:
            logger.error("Failed to write spot prices to InfluxDB")
            return None

    except Exception as e:
        logger.error(f"Exception writing spot prices to InfluxDB: {e}")
        return None


def load_status() -> dict:
    """Load status from status file."""
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE) as f:
                return json.load(f)
        else:
            logger.info("No status file found, starting fresh")
            return {"latest_epoch_timestamp": 0}
    except Exception as e:
        logger.error(f"Error loading status file: {e}")
        return {"latest_epoch_timestamp": 0}


def save_status(latest_epoch_timestamp: int) -> bool:
    """Save status to status file."""
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump({"latest_epoch_timestamp": latest_epoch_timestamp}, f)
        logger.info(f"Saved status: latest_epoch_timestamp={latest_epoch_timestamp}")
        return True
    except Exception as e:
        logger.error(f"Error saving status file: {e}")
        return False


async def collect_spot_prices(dry_run: bool = False) -> int:
    """
    Main function to collect and process spot prices.

    Args:
        dry_run: If True, don't write to InfluxDB or update status

    Returns:
        0 on success, 1 on failure
    """
    logger.info("Starting spot price collection")

    config = get_config()

    # Load status to check if we already have tomorrow's prices
    status = load_status()
    latest_uploaded_price_epoch = status.get("latest_epoch_timestamp", 0)
    current_time_epoch = int(datetime.datetime.utcnow().timestamp())

    logger.info(
        f"Latest uploaded price: "
        f"{datetime.datetime.utcfromtimestamp(latest_uploaded_price_epoch)}"
    )

    # Check if we already have prices for tomorrow
    if current_time_epoch + 86400 < latest_uploaded_price_epoch:
        logger.info("Already have prices for tomorrow. Nothing to do.")
        return 0

    # Fetch prices from API
    spot_prices_raw = await fetch_spot_prices_from_api()

    if not spot_prices_raw:
        logger.error("Failed to fetch spot prices from API")
        return 1

    # Save raw data to file (legacy backup)
    save_spot_prices_to_file(spot_prices_raw)

    # Log raw data to JSON for backup (with 1 week retention)
    json_logger = JSONDataLogger("spot_prices")
    json_logger.log_data(
        spot_prices_raw,
        metadata={"num_prices": len(spot_prices_raw), "api_url": SPOT_PRICE_API_URL},
    )
    json_logger.cleanup_old_logs()

    # Process prices
    processed_spot_prices = process_spot_prices(spot_prices_raw, config)

    if not processed_spot_prices:
        logger.error("No prices after processing")
        return 1

    # Write to InfluxDB
    latest_uploaded_price_epoch = await write_spot_prices_to_influx(
        processed_spot_prices, dry_run=dry_run
    )

    if latest_uploaded_price_epoch is None:
        logger.error("Failed to write spot prices")
        return 1

    # Check if we got tomorrow's prices
    if latest_uploaded_price_epoch > current_time_epoch + 86400:
        if not dry_run:
            save_status(latest_uploaded_price_epoch)
        logger.info("Successfully updated spot prices with tomorrow's data")
        return 0
    else:
        logger.warning(
            "Uploaded prices but didn't get tomorrow's data yet. " "Will try again later."
        )
        return 1


def main():
    """Main entry point for spot price collection."""
    import argparse

    parser = argparse.ArgumentParser(description="Collect electricity spot prices")
    parser.add_argument(
        "--dry-run", action="store_true", help="Collect prices but do not write to InfluxDB"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose debug logging")

    args = parser.parse_args()

    if args.verbose:
        import logging

        logger.setLevel(logging.DEBUG)

    if args.dry_run:
        logger.info("Starting spot price collection (DRY-RUN mode)")
    else:
        logger.info("Starting spot price collection")

    try:
        result = asyncio.run(collect_spot_prices(dry_run=args.dry_run))
        return result

    except Exception as e:
        logger.error(f"Unhandled exception in spot price collection: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
