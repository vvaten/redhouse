#!/usr/bin/env python
"""Electricity spot price data collection from spot-hinta.fi API."""

import asyncio
import datetime
import json
import os
from typing import Any, Optional

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


def _validate_config_parameters(config: Any) -> dict:
    """
    Extract and validate all required price parameters from config.

    Args:
        config: Configuration object with price parameters

    Returns:
        Dictionary with all validated price parameters

    Raises:
        ValueError: If any required parameter is missing
    """
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
            raise ValueError(f"Missing required config: {param.upper()}")

    return {
        "value_added_tax": float(config.get("spot_value_added_tax")),
        "sellers_margin": float(config.get("spot_sellers_margin")),
        "production_buyback_margin": float(config.get("spot_production_buyback_margin")),
        "transfer_day_price": float(config.get("spot_transfer_day_price")),
        "transfer_night_price": float(config.get("spot_transfer_night_price")),
        "transfer_tax_price": float(config.get("spot_transfer_tax_price")),
    }


def _parse_entry_datetime(entry: dict) -> tuple[datetime.datetime, int]:
    """
    Parse datetime from entry and handle DST edge cases.

    Args:
        entry: Entry dictionary with DateTime field

    Returns:
        Tuple of (datetime object, offset in seconds)
    """
    entry_datetime = datetime.datetime.fromisoformat(entry["DateTime"])

    # Handle DST transition (2022-10-30 specific fix from original code)
    if entry_datetime.isoformat() == "2022-10-30T03:00:00+02:00":
        offset = 3600
    else:
        offset = 0

    return entry_datetime, offset


def _format_datetime_fields(dt: datetime.datetime, epoch_timestamp: int) -> dict:
    """
    Format datetime into multiple required formats.

    Args:
        dt: Datetime object
        epoch_timestamp: Unix timestamp

    Returns:
        Dictionary with formatted datetime fields
    """
    return {
        "epoch_timestamp": epoch_timestamp,
        "datetime_utc": (
            datetime.datetime.fromtimestamp(epoch_timestamp, tz=datetime.timezone.utc)
            .replace(tzinfo=pytz.utc)
            .isoformat()
        ),
        "datetime_local": dt.isoformat(),
    }


def _determine_transfer_price(hour: int, day_price: float, night_price: float) -> float:
    """
    Determine transfer price based on time of day.

    Args:
        hour: Hour of the day (0-23)
        day_price: Day transfer price
        night_price: Night transfer price (22:00-07:00)

    Returns:
        Transfer price for the given hour
    """
    if hour >= 22 or hour < 7:
        return night_price
    else:
        return day_price


def _calculate_price_fields(entry: dict, params: dict, hour: int) -> dict:
    """
    Calculate all price fields: base, sell, with_tax, total.

    Args:
        entry: Raw price entry with PriceNoTax
        params: Dictionary with price parameters
        hour: Hour of the day for transfer price calculation

    Returns:
        Dictionary with all calculated price fields
    """
    price_no_tax = entry["PriceNoTax"]

    # Price without tax (c/kWh)
    price = price_no_tax

    # Selling price (production buyback)
    price_sell = round(price_no_tax - 0.01 * params["production_buyback_margin"], 4)

    # Price with VAT
    price_with_tax = round(params["value_added_tax"] * price_no_tax, 4)

    # Determine transfer price (night vs day rate)
    transfer_price = _determine_transfer_price(
        hour, params["transfer_day_price"], params["transfer_night_price"]
    )

    # Total price including all fees and taxes
    price_total = round(
        price_with_tax
        + 0.01 * (params["sellers_margin"] + transfer_price + params["transfer_tax_price"]),
        6,
    )

    return {
        "price": price,
        "price_sell": price_sell,
        "price_withtax": price_with_tax,
        "price_total": price_total,
    }


def process_spot_prices(spot_prices_raw: list[dict], config: Any) -> list[dict]:
    """
    Process raw spot price data and calculate final prices.

    Args:
        spot_prices_raw: Raw price data from API
        config: Configuration object with price parameters

    Returns:
        List of processed price entries with all calculations
    """
    logger.info("Processing spot prices")

    # Validate and extract price parameters
    try:
        params = _validate_config_parameters(config)
    except ValueError as e:
        logger.error(f"Required configuration parameter not set: {e}")
        raise

    processed_spot_prices = []

    for hour_entry in spot_prices_raw:
        try:
            # Parse datetime and handle DST edge cases
            entry_datetime, offset = _parse_entry_datetime(hour_entry)
            epoch_timestamp = int(entry_datetime.timestamp()) + offset

            # Build data entry with datetime fields
            data = _format_datetime_fields(entry_datetime, epoch_timestamp)

            # Calculate all price fields
            price_fields = _calculate_price_fields(hour_entry, params, entry_datetime.hour)
            data.update(price_fields)

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
            earliest_dt = datetime.datetime.fromtimestamp(
                min(entry["epoch_timestamp"] for entry in processed_spot_prices),
                tz=datetime.timezone.utc,
            )
            latest_dt = datetime.datetime.fromtimestamp(latest_timestamp, tz=datetime.timezone.utc)

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
    current_time_epoch = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    logger.info(
        f"Latest uploaded price: "
        f"{datetime.datetime.fromtimestamp(latest_uploaded_price_epoch, tz=datetime.timezone.utc)}"
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
    else:
        logger.info(
            "Uploaded prices but didn't get tomorrow's data yet. "
            "This is normal before tomorrow's prices are published (~14:00 EET)."
        )
    return 0


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
