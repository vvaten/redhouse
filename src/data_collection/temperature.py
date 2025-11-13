#!/usr/bin/env python
"""Temperature data collection from 1-wire DS18B20 sensors."""

import datetime
import os
import time
from statistics import median
from typing import Optional

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.json_logger import JSONDataLogger
from src.common.logger import setup_logger

logger = setup_logger(__name__, "temperature.log")

# Sensor ID mapping to human-readable names
SENSOR_NAMES = {
    "8a": "Hilla",
    "c1": "Niila",
    "6a": "Savupiippu",
    "3e": "Valto",
    "a0": "PaaMH",
    "c2": "Pukuhuone",
    "44": "Kirjasto",
    "f0": "Eteinen",
    "4a": "Keittio",
    "c9": "Tyohuone",
    "aa": "Leffahuone",
    "e6": "Kayttovesi ylh",
    "66": "Kayttovesi alh",
    "02": "Ulkolampo",
    "180": "Autotalli",
    "181": "PaaMH2",
    "190": "PaaMH3",
    "191": "YlakertaKH",
    "192": "KeskikerrosKH",
    "193": "AlakertaKH",
}

# Global state for previous temperature readings
_previous_temps: dict[str, float] = {}


def get_temperature_meter_ids() -> list[str]:
    """Get list of available 1-wire temperature sensor IDs.

    Returns:
        List of sensor device IDs from /sys/bus/w1/devices/
    """
    try:
        result = os.popen("ls /sys/bus/w1/devices 2> /dev/null").read()
        return result.split()
    except Exception as e:
        logger.error(f"Failed to get temperature meter IDs: {e}")
        return []


def get_temperature(meter_id: str) -> Optional[float]:
    """Read temperature from a single 1-wire sensor with validation.

    Reads the sensor multiple times to ensure stable readings. Requires
    at least 3 identical readings or falls back to median of readings
    with at least 2 occurrences.

    Args:
        meter_id: The 1-wire device ID

    Returns:
        Temperature in Celsius, or None if reading failed
    """
    global _previous_temps

    device_path = f"/sys/bus/w1/devices/{meter_id}/w1_slave"
    if not os.path.isfile(device_path):
        logger.warning(f"Device file not found: {device_path}")
        return None

    temperatures: dict[str, int] = {}
    tries = 0
    tries_max = 20
    upper_threshold = 100
    require_identical_readings = 3
    backup_identical_readings = 2
    temperature = None

    try:
        start_time = time.time()

        # Read temperature until we get consistent readings
        while tries < tries_max and temperature is None:
            tries += 1

            with open(device_path) as f:
                file_contents = f.read()

            lines = file_contents.split("\n")

            if "YES" in lines[0]:
                value_str = lines[1][(lines[1].index("=") + 1) :]
                temp_celsius = float(int(value_str) / 1000.0)

                if temp_celsius > upper_threshold:
                    time.sleep(0.01)
                    continue

                if value_str not in temperatures:
                    temperatures[value_str] = 1
                else:
                    temperatures[value_str] += 1

                if temperatures[value_str] >= require_identical_readings:
                    temperature = temp_celsius
                    break

            time.sleep(0.01)

        duration = time.time() - start_time

        logger.debug(
            f"Sensor {meter_id} readings: "
            f"{', '.join(k + ':' + str(v) for k, v in temperatures.items())}. "
            f"Tries: {tries}. Duration: {duration:.3f}s"
        )

        # Fallback: use median of readings with at least backup_identical_readings
        if temperature is None:
            temperatures_backup = []
            for k, v in temperatures.items():
                if v >= backup_identical_readings:
                    temperatures_backup.append(float(int(k) / 1000.0))

            if len(temperatures_backup) > 0:
                temperature = median(temperatures_backup)
                logger.debug(
                    f"Sensor {meter_id} using median fallback: "
                    f"{temperatures_backup} -> {temperature}"
                )

    except Exception as e:
        logger.error(f"Exception reading sensor {meter_id}: {e}")
        return None

    # Validate temperature reading
    if temperature is not None:
        # DS18B20 valid range
        if not (-55 <= temperature <= 125):
            logger.warning(f"Sensor {meter_id} reading {temperature} out of valid range")
            return None

        # Suspicious values (common error codes)
        if temperature == 85 or temperature == 0:
            prev_temp = _previous_temps.get(meter_id)
            if prev_temp != temperature or prev_temp is None:
                logger.warning(f"Sensor {meter_id} suspicious reading: {temperature}")
                return None

        _previous_temps[meter_id] = temperature
        return temperature
    else:
        logger.warning(f"Failed to read temperature from sensor: {meter_id}")
        return None


def convert_internal_id_to_influxid(internal_id: str) -> Optional[str]:
    """Convert internal sensor ID to InfluxDB field name.

    Args:
        internal_id: Full sensor ID (e.g., '28-...6a')

    Returns:
        Human-readable sensor name, or None if not found
    """
    if str(internal_id)[:2] == "28":
        conversion_id = internal_id[-2:]
    elif str(internal_id)[:6] == "shelly":
        conversion_id = internal_id[-3:]
    elif str(internal_id)[-4:-1] == "-19":
        conversion_id = internal_id[-3:]
    else:
        logger.warning(f"Unknown internal_id type: {internal_id}")
        return None

    if conversion_id in SENSOR_NAMES:
        return SENSOR_NAMES.get(conversion_id)
    else:
        logger.warning(
            f"No conversion found for internal_id {internal_id} "
            f"(conversion_id: {conversion_id})"
        )
        return None


def collect_temperatures() -> dict[str, dict[str, float]]:
    """Collect temperatures from all available 1-wire sensors.

    Returns:
        Dictionary mapping sensor IDs to temperature data with format:
        {
            'sensor_id': {
                'temp': temperature_celsius,
                'updated': unix_timestamp
            }
        }
    """
    temperature_status = {}
    meter_ids = get_temperature_meter_ids()

    logger.info(f"Found {len(meter_ids)} temperature sensors")

    for meter_id in meter_ids:
        # Skip faulty sensors
        if meter_id.endswith("e9"):
            logger.debug(f"Skipping faulty sensor: {meter_id}")
            continue

        temp = get_temperature(meter_id)

        if temp is not None:
            temperature_status[meter_id] = {"temp": temp, "updated": time.time()}

    # Log raw data to JSON for backup (30 days retention for local sensor data)
    json_logger = JSONDataLogger("temperature")
    json_logger.retention_days = 30
    json_logger.log_data(
        temperature_status,
        metadata={"num_sensors": len(temperature_status), "timestamp": time.time()},
    )
    json_logger.cleanup_old_logs()

    logger.info(f"Successfully read {len(temperature_status)} temperatures")
    return temperature_status


def write_temperatures_to_influx(
    temperature_status: dict[str, dict[str, float]], dry_run: bool = False
) -> bool:
    """Write temperature data to InfluxDB.

    Args:
        temperature_status: Temperature data from collect_temperatures()
        dry_run: If True, only log what would be written without actually writing

    Returns:
        True if successful, False otherwise
    """
    config = get_config()

    try:
        # Prepare temperature fields
        temp_fields = {}
        for temp_id, temp_data in temperature_status.items():
            influx_id = convert_internal_id_to_influxid(temp_id)
            if influx_id is not None:
                temp_fields[influx_id] = float(temp_data["temp"])

        if not temp_fields:
            logger.warning("No valid temperature fields to write")
            return False

        timestamp = datetime.datetime.utcnow()

        if dry_run:
            logger.info(f"[DRY-RUN] Would write {len(temp_fields)} temperatures to InfluxDB:")
            for field_name, value in temp_fields.items():
                logger.info(f"[DRY-RUN]   {field_name}: {value} C")
            logger.info(f"[DRY-RUN] Timestamp: {timestamp}")
            logger.info(f"[DRY-RUN] Bucket: {config.influxdb_bucket_temperatures}")
            return True

        # Write to InfluxDB
        influx = InfluxClient(config)
        success = influx.write_point(
            measurement="temperatures", fields=temp_fields, timestamp=timestamp
        )

        if success:
            logger.info(f"Wrote {len(temp_fields)} temperatures to InfluxDB at {timestamp}")
        else:
            logger.error("Failed to write temperatures to InfluxDB")

        return success

    except Exception as e:
        logger.error(f"Exception writing temperatures to InfluxDB: {e}")
        return False


def main():
    """Main entry point for temperature collection."""
    import argparse

    parser = argparse.ArgumentParser(description="Collect temperatures from 1-wire sensors")
    parser.add_argument(
        "--dry-run", action="store_true", help="Collect temperatures but do not write to InfluxDB"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose debug logging")

    args = parser.parse_args()

    if args.verbose:
        import logging

        logger.setLevel(logging.DEBUG)

    # CRITICAL: Temperature collection should NOT run in staging mode
    # to avoid hardware sensor contention with production system
    staging_mode = os.getenv("STAGING_MODE", "false").lower() in ("true", "1", "yes")
    if staging_mode:
        logger.info(
            "STAGING MODE enabled - temperature collection DISABLED "
            "(avoiding hardware sensor contention with production)"
        )
        return 0

    if args.dry_run:
        logger.info("Starting temperature collection (DRY-RUN mode)")
    else:
        logger.info("Starting temperature collection")

    try:
        temperature_status = collect_temperatures()

        if not temperature_status:
            logger.warning("No temperatures collected")
            return 1

        success = write_temperatures_to_influx(temperature_status, dry_run=args.dry_run)

        if success:
            if args.dry_run:
                logger.info("Temperature collection DRY-RUN completed successfully")
            else:
                logger.info("Temperature collection completed successfully")
            return 0
        else:
            logger.error("Temperature collection failed")
            return 1

    except Exception as e:
        logger.error(f"Unhandled exception in temperature collection: {e}")
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
