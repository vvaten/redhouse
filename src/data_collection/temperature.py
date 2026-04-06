#!/usr/bin/env python
"""Temperature data collection from 1-wire DS18B20 and Shelly HT sensors."""

import datetime
import json
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


def _read_sensor_once(device_path: str, upper_threshold: float) -> Optional[str]:
    """Read sensor value once and return raw value string if valid.

    Args:
        device_path: Path to sensor device file
        upper_threshold: Upper temperature threshold to reject readings

    Returns:
        Raw temperature value string from sensor, or None if invalid
    """
    try:
        with open(device_path) as f:
            file_contents = f.read()

        lines = file_contents.split("\n")

        if "YES" in lines[0]:
            value_str = lines[1][(lines[1].index("=") + 1) :]
            temp_celsius = float(int(value_str) / 1000.0)

            if temp_celsius > upper_threshold:
                return None

            return value_str

        return None

    except Exception:
        return None


def _collect_stable_readings(
    device_path: str, require_identical: int, max_tries: int, upper_threshold: float
) -> tuple[dict[str, int], int]:
    """Collect multiple sensor readings until stable values are found.

    Args:
        device_path: Path to sensor device file
        require_identical: Number of identical readings required for success
        max_tries: Maximum number of read attempts
        upper_threshold: Upper temperature threshold to reject readings

    Returns:
        Tuple of (readings_dict, tries_count) where readings_dict maps
        raw value strings to occurrence counts
    """
    temperatures: dict[str, int] = {}
    tries = 0

    while tries < max_tries:
        tries += 1

        value_str = _read_sensor_once(device_path, upper_threshold)
        if value_str is not None:
            if value_str not in temperatures:
                temperatures[value_str] = 1
            else:
                temperatures[value_str] += 1

            if temperatures[value_str] >= require_identical:
                break

        time.sleep(0.01)

    return temperatures, tries


def _calculate_fallback_temperature(
    temperatures: dict[str, int], backup_identical_readings: int, meter_id: str
) -> Optional[float]:
    """Calculate fallback temperature using median of repeated readings.

    Args:
        temperatures: Dictionary mapping raw value strings to occurrence counts
        backup_identical_readings: Minimum occurrences required for fallback
        meter_id: Sensor ID for logging

    Returns:
        Median temperature in Celsius, or None if no valid fallback
    """
    temperatures_backup = []
    for k, v in temperatures.items():
        if v >= backup_identical_readings:
            temperatures_backup.append(float(int(k) / 1000.0))

    if len(temperatures_backup) > 0:
        temperature = median(temperatures_backup)
        logger.debug(
            f"Sensor {meter_id} using median fallback: " f"{temperatures_backup} -> {temperature}"
        )
        return temperature

    return None


def _validate_temperature_reading(
    temperature: float, meter_id: str, previous_temps: dict[str, float]
) -> bool:
    """Validate temperature reading is within acceptable range.

    Args:
        temperature: Temperature value to validate
        meter_id: Sensor ID for logging
        previous_temps: Dictionary of previous temperature readings

    Returns:
        True if temperature is valid, False otherwise
    """
    # DS18B20 valid range
    if not (-55 <= temperature <= 125):
        logger.warning(f"Sensor {meter_id} reading {temperature} out of valid range")
        return False

    # Suspicious values (common error codes)
    if temperature == 85 or temperature == 0:
        prev_temp = previous_temps.get(meter_id)
        if prev_temp != temperature or prev_temp is None:
            logger.warning(f"Sensor {meter_id} suspicious reading: {temperature}")
            return False

    return True


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

    tries_max = 20
    upper_threshold = 100
    require_identical_readings = 3
    backup_identical_readings = 2

    try:
        start_time = time.time()

        # Collect stable readings
        temperatures, tries = _collect_stable_readings(
            device_path, require_identical_readings, tries_max, upper_threshold
        )

        duration = time.time() - start_time

        logger.debug(
            f"Sensor {meter_id} readings: "
            f"{', '.join(k + ':' + str(v) for k, v in temperatures.items())}. "
            f"Tries: {tries}. Duration: {duration:.3f}s"
        )

        # Calculate temperature with primary or fallback method
        temperature = None
        for value_str, count in temperatures.items():
            if count >= require_identical_readings:
                temperature = float(int(value_str) / 1000.0)
                break

        if temperature is None:
            temperature = _calculate_fallback_temperature(
                temperatures, backup_identical_readings, meter_id
            )

    except Exception as e:
        logger.error(f"Exception reading sensor {meter_id}: {e}")
        return None

    # Validate and return temperature
    if temperature is not None:
        if _validate_temperature_reading(temperature, meter_id, _previous_temps):
            _previous_temps[meter_id] = temperature
            return temperature
        else:
            return None
    else:
        logger.warning(f"Failed to read temperature from sensor: {meter_id}")
        return None


def convert_internal_id_to_influxid(internal_id: str) -> Optional[str]:
    """Convert internal sensor ID to InfluxDB field name using sensors.yaml.

    Looks up the sensor ID in the config sensor_mapping. Tries direct match
    first, then suffix matching (last 2 chars for DS18B20, last 3 for Shelly).
    """
    config = get_config()
    sensor_mapping = config.sensor_mapping

    # Direct lookup
    if internal_id in sensor_mapping:
        return sensor_mapping[internal_id]

    # Suffix matching for DS18B20 (last 2 chars)
    if internal_id.startswith("28-"):
        suffix = internal_id[-2:]
        for key, value in sensor_mapping.items():
            if key.endswith(suffix):
                return value

    # Suffix matching for Shelly and other sensors (last 3 chars)
    if internal_id.startswith("shelly") or "-19" in internal_id[-4:]:
        suffix = internal_id[-3:]
        for key, value in sensor_mapping.items():
            if key.endswith(suffix):
                return value

    logger.warning(f"No sensor mapping found for: {internal_id}")
    return None


# Shelly HT: max 24h stale (energy saving mode only updates on value change)
SHELLY_HT_MAX_AGE_SECONDS = 86400
# Path to Shelly HT status file (written by shelly_ht_to_fissio_rest_api.py)
SHELLY_HT_STATUS_FILE = "/home/pi/wibatemp/temperature_status.json"


def load_shelly_ht_data(status_file: str = SHELLY_HT_STATUS_FILE) -> dict[str, dict]:
    """Load Shelly HT sensor data from temperature_status.json."""
    if not os.path.exists(status_file):
        logger.debug(f"Shelly HT status file not found: {status_file}")
        return {}

    try:
        with open(status_file) as f:
            all_data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to read Shelly HT status file: {e}")
        return {}

    now = time.time()
    shelly_data = {}

    for sensor_id, data in all_data.items():
        # Skip 1-wire sensors (those are read directly)
        if sensor_id.startswith("28-"):
            continue

        # Skip stale data
        updated = data.get("updated", 0)
        age = now - updated
        if age > SHELLY_HT_MAX_AGE_SECONDS:
            logger.warning(
                f"Shelly HT sensor {sensor_id} stale for {age / 3600:.1f}h "
                f"- check battery or reset device"
            )
            continue

        if data.get("temp") is not None:
            shelly_data[sensor_id] = data
            logger.debug(
                f"Loaded Shelly HT: {sensor_id} temp={data.get('temp')} hum={data.get('hum')}"
            )

    if shelly_data:
        logger.info(f"Loaded {len(shelly_data)} Shelly HT sensors from {status_file}")

    return shelly_data


def collect_temperatures() -> dict[str, dict[str, float]]:
    """Collect temperatures from 1-wire DS18B20 and Shelly HT sensors."""
    temperature_status = {}
    meter_ids = get_temperature_meter_ids()

    logger.info(f"Found {len(meter_ids)} 1-wire temperature sensors")

    for meter_id in meter_ids:
        # Skip faulty sensors
        if meter_id.endswith("e9"):
            logger.debug(f"Skipping faulty sensor: {meter_id}")
            continue

        temp = get_temperature(meter_id)

        if temp is not None:
            temperature_status[meter_id] = {"temp": temp, "updated": time.time()}

    # Merge Shelly HT data (temp + humidity)
    shelly_data = load_shelly_ht_data()
    temperature_status.update(shelly_data)

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


def _prepare_influx_fields(
    temperature_status: dict[str, dict],
) -> tuple[dict[str, float], dict[str, float]]:
    """Convert sensor data to InfluxDB field dicts.

    Returns:
        Tuple of (temp_fields, hum_fields) with human-readable sensor names as keys.
    """
    temp_fields = {}
    hum_fields = {}
    for temp_id, temp_data in temperature_status.items():
        influx_id = convert_internal_id_to_influxid(temp_id)
        if influx_id is None:
            continue
        if temp_data.get("temp") is not None:
            temp_fields[influx_id] = float(temp_data["temp"])
        if temp_data.get("hum") is not None:
            hum_fields[influx_id] = float(temp_data["hum"])
    return temp_fields, hum_fields


def write_temperatures_to_influx(
    temperature_status: dict[str, dict[str, float]],
    dry_run: bool = False,
    timestamp: Optional[datetime.datetime] = None,
) -> bool:
    """Write temperature and humidity data to InfluxDB.

    Writes temperatures to 'temperatures' measurement and humidity
    to 'humidities' measurement (same bucket), matching wibatemp format.
    """
    config = get_config()

    try:
        temp_fields, hum_fields = _prepare_influx_fields(temperature_status)

        if not temp_fields:
            logger.warning("No valid temperature fields to write")
            return False

        if timestamp is None:
            timestamp = datetime.datetime.utcnow()

        if dry_run:
            logger.info(f"[DRY-RUN] Would write {len(temp_fields)} temperatures to InfluxDB")
            if hum_fields:
                logger.info(f"[DRY-RUN] Would write {len(hum_fields)} humidities to InfluxDB")
            logger.info(f"[DRY-RUN] Bucket: {config.influxdb_bucket_temperatures}")
            return True

        influx = InfluxClient(config)

        success = influx.write_point(
            measurement="temperatures", fields=temp_fields, timestamp=timestamp
        )
        if success:
            logger.info(f"Wrote {len(temp_fields)} temperatures to InfluxDB at {timestamp}")
        else:
            logger.error("Failed to write temperatures to InfluxDB")
            return False

        if hum_fields:
            hum_success = influx.write_point(
                measurement="humidities", fields=hum_fields, timestamp=timestamp
            )
            if hum_success:
                logger.info(f"Wrote {len(hum_fields)} humidities to InfluxDB")
            else:
                logger.warning("Failed to write humidities to InfluxDB")

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
