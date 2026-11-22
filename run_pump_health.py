#!/usr/bin/env python
"""Pump health check - wrapper script for systemd, every 5 minutes.

Mails when the pump is ON but heats with the heater alone and the tank does
not climb: the state after the high-pressure alarm of 2026-11-16. Staging
only logs.
"""

import datetime
import json
import sys
import time
from pathlib import Path
from typing import Optional

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger
from src.control import hot_water_controller as hwc
from src.control import hot_water_io as hio
from src.monitoring import pump_health as ph
from src.monitoring.alert_mail import is_staging, mailer

logger = setup_logger(__name__, "pump_health.log")

DATA = Path(__file__).resolve().parent / "data"
ALERT_STATE = DATA / "pump_health_alert.json"
REPEAT_S = 6 * 3600
INFLUX_TIMEOUT_MS = 10_000


def program_on_since(entries: list, now: int) -> Optional[int]:
    """The start of the program's ON block that runs now, or None."""
    start = None
    for at, command in entries:
        if at > now:
            break
        if command != "ON":
            start = None
        elif start is None:
            start = at
    return start


def pump_on_since(path: Path, entries: list, now: int) -> Optional[int]:
    """When the pump entered ON; None when it is not ON. The program's block
    start wins, as each EVU pulse resets the state file's on_since."""
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        logger.error(f"Pump state unreadable: {e}")
        return None
    if "last_command" not in state or "on_since" not in state:
        logger.error(f"{path} lacks last_command or on_since")
        return None
    if state["last_command"] != "ON" or state["on_since"] is None:
        return None
    block = program_on_since(entries, now)
    return block if block is not None else int(state["on_since"])


def outdoor_now(influx, config, now: int) -> Optional[float]:
    """The weather bucket's air temperature for the current hour."""
    hour = now - now % 3600
    flux = f"""
        from(bucket: "{config.influxdb_bucket_weather}")
          |> range(start: {hio.rfc3339(hour - 3600)}, stop: {hio.rfc3339(hour + 3601)})
          |> filter(fn: (r) => r._measurement == "weather" and r._field == "Air temperature")
    """
    values = [
        (abs(int(rec.get_time().timestamp()) - hour), float(rec.get_value()))
        for table in influx.query_with_retry(flux)
        for rec in table.records
        if rec.get_value() is not None
    ]
    return min(values)[1] if values else None


def should_mail(now: float) -> bool:
    try:
        sent = float(json.loads(ALERT_STATE.read_text())["sent"])
    except (OSError, ValueError, KeyError, TypeError):
        return True
    return now - sent >= REPEAT_S


def main() -> int:
    config = get_config()
    settings = ph.load_health_settings(config.pump_health_section)
    hw = hwc.load_settings(config.hot_water_section, is_staging(config))
    staging = is_staging(config)
    now = int(time.time())
    influx = InfluxClient(config, timeout_ms=INFLUX_TIMEOUT_MS)
    try:
        minutes = hio.read_minutes(influx, config, hw, now - ph.BASE_WINDOW_S, now + 60)
        outdoor = outdoor_now(influx, config, now)
    except Exception as e:
        logger.error(f"Pump health data unavailable: {e}")
        return 1
    state_file = DATA / ("pump_state_dry_run.json" if staging else "pump_state.json")
    on_since = pump_on_since(state_file, hio.program_entries(now), now)
    verdict = ph.judge(minutes, now, on_since, outdoor, settings)
    logger.info(
        f"Pump health: {verdict.reason} (heater {verdict.heater_min} min, outdoor {outdoor})"
    )
    if not verdict.alarm or not should_mail(now):
        return 0
    stamp = datetime.datetime.now().isoformat(timespec="minutes")
    body = (
        f"{stamp}: the pump is ON but has heated with the heater alone for "
        f"{verdict.heater_min} of the last {settings.no_compressor_min} min, and the tank "
        "does not climb. Check the pump display for an alarm (high pressure: clean the "
        "filter, check the network pressure, reset the alarm)."
    )
    if mailer(config, staging)("[RedHouse WARNING] Heat pump: heater only", body):
        DATA.mkdir(parents=True, exist_ok=True)
        ALERT_STATE.write_text(json.dumps({"sent": now}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
