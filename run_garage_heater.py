#!/usr/bin/env python
"""Garage heater tick - wrapper script for systemd.

Usage:
    python run_garage_heater.py [--dry-run]

STAGING_MODE or --dry-run use mock relays. A dry run in production writes
its decision with data_type "dry_run"; staging writes to its own bucket.
"""

import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

from src.common.atomic_json import write_json
from src.common.config import get_config
from src.common.control_lock import ControlLockTimeout, control_lock
from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger
from src.control import garage_controller as gc
from src.control.hardware_implementations import GarageRelay, MockGarageRelay
from src.control.load_overrides import GARAGE, active_override
from src.data_collection.temperature import convert_internal_id_to_influxid
from src.monitoring.email_sender import send_alert_email

logger = setup_logger(__name__, "garage_heater.log")

# The tick starts with the executor, which can hold the lock for a minute
LOCK_TIMEOUT_S = 90
INFLUX_TIMEOUT_MS = 10_000
ALERT_STATE = Path(__file__).resolve().parent / "data" / "garage_alert_state.json"
OVERRIDES = Path(__file__).resolve().parent / "data" / "load_overrides.json"
# The load guard reads whether the heaters are on from here
GARAGE_STATE = Path(__file__).resolve().parent / "data" / "garage_state.json"
ALERT_REPEAT_S = 6 * 3600
COLD_MARGIN_K = 0.5


def _staging(config) -> bool:
    """Staging by its flag or by its buckets, so a lost flag keeps the mocks."""
    flag = os.getenv("STAGING_MODE", "false").lower() in ("true", "1", "yes")
    return flag or config.influxdb_bucket_load_control.endswith("_staging")


def _alert(config, problem: str, detail: str) -> None:
    """Mail a problem at most once per ALERT_REPEAT_S. Staging only logs it."""
    if _staging(config):
        logger.warning(f"Garage alert (staging, not mailed): {problem}: {detail}")
        return
    try:
        sent = json.loads(ALERT_STATE.read_text()) if ALERT_STATE.exists() else {}
    except (OSError, ValueError):
        sent = {}
    if problem in sent and time.time() - sent[problem] < ALERT_REPEAT_S:
        return
    api_key = config.get("RESEND_API_KEY")
    to_email = config.get("ALERT_EMAIL_TO")
    if not api_key or not to_email:
        logger.error(f"Cannot mail the garage alert {problem}: no email settings")
        return
    from_email = config.get("ALERT_EMAIL_FROM")
    kwargs = {"from_email": from_email} if from_email else {}
    subject = f"[RedHouse WARNING] Garage heater: {problem}"
    if send_alert_email(api_key, to_email, subject, detail, **kwargs):
        sent[problem] = time.time()
        try:
            ALERT_STATE.parent.mkdir(parents=True, exist_ok=True)
            ALERT_STATE.write_text(json.dumps(sent))
        except OSError as e:
            logger.error(f"Garage alert state not saved: {e}")


def _io(config, settings, influx, dry_run: bool) -> gc.GarageIO:
    def write(point):
        if dry_run and not _staging(config):
            point = point.tag("data_type", "dry_run")
        try:
            influx.write_with_retry(bucket=config.influxdb_bucket_load_control, record=point)
        except Exception as e:
            logger.error(f"Garage point not written: {e}")

    return gc.GarageIO(
        read_reading=lambda s: gc.read_reading(
            s.status_file, s.sensor_name, convert_internal_id_to_influxid
        ),
        fetch_inputs=lambda start: gc.fetch_inputs(influx, config, start),
        lock=lambda: control_lock(LOCK_TIMEOUT_S),
        write=write,
        override_off=lambda now: active_override(OVERRIDES, GARAGE, now) is not None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Garage heater tick")
    parser.add_argument("--dry-run", action="store_true", help="Use mock relays")
    args = parser.parse_args()
    config = get_config()
    settings = gc.load_settings(config.garage_section)
    mock = args.dry_run or _staging(config)
    relay_type = MockGarageRelay if mock else GarageRelay
    relays = [relay_type(url) for url in settings.relay_urls]
    influx = InfluxClient(config, timeout_ms=INFLUX_TIMEOUT_MS)
    try:
        result = gc.run_once(time.time(), settings, relays, _io(config, settings, influx, mock))
    except ControlLockTimeout as e:
        logger.error(f"Relays not set, the control lock is taken: {e}")
        return 1
    except OSError as e:
        logger.error(f"Relays not set, cannot open the control lock: {e}")
        return 1
    # A manual dry run in production must not overwrite the real state
    if not (args.dry_run and not _staging(config)):
        try:
            on = any(s is True for s in result.relay_states)
            state = {"on": on, "t": time.time(), "reason": result.decision.reason}
            write_json(GARAGE_STATE, state)
        except OSError as e:
            logger.error(f"Garage state not saved: {e}")
    stamp = datetime.datetime.now().isoformat(timespec="minutes")
    if result.decision.reason == "stale_reading":
        _alert(config, "stale reading", f"{stamp}: no fresh garage reading, heating ON")
    if not result.success:
        _alert(config, "relay failure", f"{stamp}: relays {result.relay_states}")
    # Relays that switch do not prove heat: the wall thermostat can be off
    if result.reading_c is not None and result.reading_c < settings.params.floor_c - COLD_MARGIN_K:
        detail = f"{stamp}: garage {result.reading_c} C, relays {result.relay_states}"
        _alert(config, "garage cold", detail)
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
