"""Pytest fixtures for integration tests.

They need a real InfluxDB: REDHOUSE_INTEGRATION=1 pytest tests/integration/
"""

import datetime
import os

import pytest
import pytz
from influxdb_client import Point

from src.common.config import Config, get_config
from src.common.influx_client import InfluxClient

OPT_IN_VAR = "REDHOUSE_INTEGRATION"

if os.environ.get(OPT_IN_VAR) != "1":
    collect_ignore_glob = ["test_*.py"]

# One entry per influxdb_bucket_* property of Config. A unit test fails
# when a new property has no entry here.
TEST_BUCKETS = {
    "INFLUXDB_BUCKET_TEMPERATURES": "temperatures_test",
    "INFLUXDB_BUCKET_WEATHER": "weather_test",
    "INFLUXDB_BUCKET_SPOTPRICE": "spotprice_test",
    "INFLUXDB_BUCKET_EMETERS": "emeters_test",
    "INFLUXDB_BUCKET_CHECKWATT": "checkwatt_test",
    "INFLUXDB_BUCKET_SHELLY_EM3_RAW": "shelly_em3_emeters_raw_test",
    "INFLUXDB_BUCKET_EMETERS_5MIN": "emeters_5min_test",
    "INFLUXDB_BUCKET_ANALYTICS_15MIN": "analytics_15min_test",
    "INFLUXDB_BUCKET_ANALYTICS_1HOUR": "analytics_1hour_test",
    "INFLUXDB_BUCKET_WINDPOWER": "windpower_test",
    "INFLUXDB_BUCKET_LOAD_CONTROL": "load_control_test",
}


def bucket_properties() -> list:
    """Names of every bucket property on Config."""
    return sorted(name for name in dir(Config) if name.startswith("influxdb_bucket_"))


def non_test_buckets(config: Config) -> list:
    """Bucket properties that do not resolve to a *_test bucket."""
    offenders = []
    for name in bucket_properties():
        try:
            bucket = getattr(config, name)
        except ValueError:
            offenders.append(f"{name} (unset)")
            continue
        if not bucket.endswith("_test"):
            offenders.append(f"{name}={bucket}")
    return offenders


@pytest.fixture(scope="session", autouse=True)
def isolate_buckets():
    """Point every bucket at its test copy, or skip or abort the run.
    Autouse: a test that calls get_config() never asks for config. The
    skip covers paths named on the command line, which bypass the glob."""
    if os.environ.get(OPT_IN_VAR) != "1":
        pytest.skip(f"integration tests need {OPT_IN_VAR}=1")

    saved = {key: os.environ.get(key) for key in TEST_BUCKETS}
    os.environ.update(TEST_BUCKETS)
    offenders = non_test_buckets(get_config())
    if offenders:
        pytest.exit(f"Refusing to run against real buckets: {offenders}", returncode=1)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key)
        else:
            os.environ[key] = value


@pytest.fixture(scope="session")
def config(isolate_buckets):
    """Get config with test environment variables."""
    return get_config()


@pytest.fixture(scope="session")
def influx_client(config):
    """Get InfluxDB client for tests."""
    return InfluxClient(config)


@pytest.fixture
def test_timestamp():
    """Return a consistent test timestamp (always use current time to avoid retention policy issues)."""
    now = datetime.datetime.now(pytz.UTC)
    # Round to nearest hour for consistency
    return now.replace(minute=0, second=0, microsecond=0)


@pytest.fixture
def cleanup_test_data(influx_client, config):
    """Cleanup test data before and after each test."""
    import time

    # Cleanup BEFORE test to ensure clean state
    buckets_to_clean = [
        config.influxdb_bucket_checkwatt,
        config.influxdb_bucket_shelly_em3_raw,
        config.influxdb_bucket_emeters_5min,
        config.influxdb_bucket_analytics_15min,
        config.influxdb_bucket_analytics_1hour,
        config.influxdb_bucket_spotprice,
        config.influxdb_bucket_temperatures,
    ]
    delete_api = influx_client.client.delete_api()
    start = "2026-01-01T00:00:00Z"
    stop = "2026-12-31T23:59:59Z"

    for bucket in buckets_to_clean:
        try:
            delete_api.delete(
                start=start,
                stop=stop,
                predicate="",  # Delete everything, not just specific measurements
                bucket=bucket,
                org=config.influxdb_org,
            )
        except Exception:
            pass  # Bucket might not exist or have no data

    # Flush write buffer and wait for deletes to complete
    influx_client.write_api.flush()
    time.sleep(1.0)  # Increased wait time for InfluxDB to process deletes

    yield

    # Cleanup AFTER test
    for bucket in buckets_to_clean:
        try:
            delete_api.delete(
                start=start,
                stop=stop,
                predicate="",  # Delete everything
                bucket=bucket,
                org=config.influxdb_org,
            )
        except Exception:
            pass  # Bucket might not exist or have no data

    # Flush after cleanup too
    influx_client.write_api.flush()


def write_checkwatt_data(
    influx_client,
    config,
    timestamp,
    consumption=1000,
    solar=500,
    battery_discharge=200,
    battery_soc=65,
    battery_charge=0,
):
    """Write synthetic CheckWatt data for testing.

    Args:
        consumption: Power consumption in W
        solar: Solar production in W
        battery_discharge: Battery discharge power in W
        battery_charge: Battery charge power in W
        battery_soc: Battery state of charge in %

    CheckWatt data with "delta" grouping returns AVERAGE POWER (W) for that minute, not energy deltas.
    The aggregator then averages these power values across 5-minute windows.
    """
    # Grid power depends on net flow
    net_power = consumption - solar + battery_charge - battery_discharge
    grid_import_power = max(0.0, net_power)
    grid_export_power = max(0.0, -net_power)

    point = (
        Point("checkwatt_v2")
        .time(timestamp)
        .field("SolarYield", float(solar))
        .field("BatteryDischarge", float(battery_discharge))
        .field("BatteryCharge", float(battery_charge))
        .field("Battery_SoC", float(battery_soc))
        .field("EnergyImport", float(grid_import_power))
        .field("EnergyExport", float(grid_export_power))
    )
    influx_client.write_api.write(
        bucket=config.influxdb_bucket_checkwatt,
        org=config.influxdb_org,
        record=point,
    )


def write_shelly_data(
    influx_client,
    config,
    timestamp,
    phase_a=300.0,
    phase_b=300.0,
    phase_c=300.0,
    baseline_timestamp=None,
):
    """Write synthetic Shelly EM3 data for testing.

    Args:
        phase_a, phase_b, phase_c: Instantaneous power per phase in W (can be negative for export)
        baseline_timestamp: Reference time for cumulative energy calculation. If None, uses timestamp.

    The cumulative energy fields are calculated assuming constant power from baseline to current timestamp.
    """
    if baseline_timestamp is None:
        baseline_timestamp = timestamp

    total = float(phase_a + phase_b + phase_c)

    # Calculate cumulative energy (Wh) from baseline
    # Energy = Power (W) × Time (hours)
    # Shelly keeps separate monotonic counters for import and export
    elapsed_hours = (timestamp - baseline_timestamp).total_seconds() / 3600.0

    # Use 101 Wh baseline to satisfy aggregator sanity check (>= 100 Wh)
    # Both counters always increase, net is the signed difference
    if total >= 0:
        # Importing: total_energy increases, returned stays at baseline
        total_energy = 101.0 + (total * elapsed_hours)
        total_energy_returned = 101.0
    else:
        # Exporting: total stays at baseline, returned increases
        total_energy = 101.0
        total_energy_returned = 101.0 + (abs(total) * elapsed_hours)

    net_total_energy = total_energy - total_energy_returned

    point = (
        Point("shelly_em3")
        .time(timestamp)
        .field("total_power", total)
        .field("phase1_power", phase_a)
        .field("phase2_power", phase_b)
        .field("phase3_power", phase_c)
        .field("phase1_current", abs(phase_a) / 230.0)
        .field("phase2_current", abs(phase_b) / 230.0)
        .field("phase3_current", abs(phase_c) / 230.0)
        .field("phase1_voltage", 230.0)
        .field("phase2_voltage", 230.0)
        .field("phase3_voltage", 230.0)
        .field("phase1_pf", 1.0 if phase_a >= 0 else -1.0)
        .field("phase2_pf", 1.0 if phase_b >= 0 else -1.0)
        .field("phase3_pf", 1.0 if phase_c >= 0 else -1.0)
        # Cumulative energy totals
        .field("total_energy", float(total_energy))
        .field("total_energy_returned", float(total_energy_returned))
        .field("net_total_energy", float(net_total_energy))
    )
    influx_client.write_api.write(
        bucket=config.influxdb_bucket_shelly_em3_raw,
        org=config.influxdb_org,
        record=point,
    )


def write_temperature_data(influx_client, config, timestamp, outdoor_temp=5.0, indoor_temp=21.0):
    """Write synthetic temperature data for testing.

    Args:
        outdoor_temp: Outdoor temperature in C
        indoor_temp: Average indoor temperature in C (will vary slightly per room)
    """
    # Create realistic temperature variations for all 19 sensors
    temps = {
        # Outdoor/utility
        "Ulkolampo": outdoor_temp,
        "Autotalli": outdoor_temp + 2.0,  # Garage slightly warmer
        "Savupiippu": outdoor_temp + 5.0,  # Chimney
        # System/technical sensors
        "PaaMH2": 33.0,  # Heat pump related
        "PaaMH3": 34.0,
        # Hot water
        "Kayttovesi ylh": 55.0,  # Hot water upper
        "Kayttovesi alh": 50.0,  # Hot water lower
        # Indoor rooms - bedrooms
        "PaaMH": indoor_temp,  # Pää Makuuhuone (Master Bedroom)
        "Valto": indoor_temp,  # Kids room
        "Niila": indoor_temp,  # Kids room
        "Hilla": indoor_temp,  # Kids room
        # Indoor rooms - other
        "Keittio": indoor_temp + 1.0,  # Kitchen warmer
        "Tyohuone": indoor_temp,
        "Kirjasto": indoor_temp - 0.5,
        "Eteinen": indoor_temp - 1.0,  # Entrance cooler
        "Pukuhuone": indoor_temp,
        "Leffahuone": indoor_temp,
        "YlakertaKH": indoor_temp + 0.5,
        "KeskikerrosKH": indoor_temp,
        "AlakertaKH": indoor_temp - 0.5,
    }

    point = Point("temperatures").time(timestamp)
    for field_name, value in temps.items():
        point = point.field(field_name, float(value))

    influx_client.write_api.write(
        bucket=config.influxdb_bucket_temperatures,
        org=config.influxdb_org,
        record=point,
    )
