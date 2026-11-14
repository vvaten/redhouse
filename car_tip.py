#!/usr/bin/env python
"""Car charging tip - wrapper script for systemd, daily after the prices.

Mails the cheapest window to charge the car before the morning deadline,
when it saves enough against the usual evening start. Staging only logs.

Usage:
    python car_tip.py [--dry-run]
"""

import argparse
import datetime
import os
import sys
from pathlib import Path

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.local_time import HELSINKI
from src.common.logger import setup_logger
from src.control import car_tip as ct
from src.control import load_budget as lb
from src.control import power_cap as pc
from src.control.program_fallback import load_valid_program
from src.control.program_shape import PUMP_LOAD
from src.monitoring.email_sender import send_alert_email
from src.monitoring.program_check import PROGRAM_BASE_DIR, program_path

logger = setup_logger(__name__, "car_tip.log")

CAP_STATE = Path(__file__).resolve().parent / "data" / "power_cap_state.json"
UTC = datetime.timezone.utc
HORIZON_H = 24


def planned_pump_kw(programs: list, pump_on_kw: float) -> dict:
    """{hour start: kW} of the pump's ON entries in the given programs."""
    planned: dict = {}
    for program in programs:
        if program is None or PUMP_LOAD not in program["loads"]:
            continue
        for entry in program["loads"][PUMP_LOAD]["schedule"]:
            if entry["command"] != "ON":
                continue
            minutes = entry["duration_minutes"] if entry["duration_minutes"] else 60
            hour = datetime.datetime.fromtimestamp(entry["timestamp"], UTC)
            hour = hour.replace(minute=0, second=0, microsecond=0)
            earlier = planned[hour] if hour in planned else 0.0
            planned[hour] = earlier + pump_on_kw * minutes / 60
    return planned


def context(now: datetime.datetime, budget, cap) -> dict:
    """The other load of each hour and the month's counted hours so far."""
    today = now.astimezone(HELSINKI).date()
    programs = [
        load_valid_program(program_path(day, PROGRAM_BASE_DIR))
        for day in (today, today + datetime.timedelta(days=1))
    ]
    pump = planned_pump_kw(programs, budget.pump_on_kw)
    start = now.replace(minute=0, second=0, microsecond=0)
    other = {}
    for i in range(HORIZON_H):
        hour = start + datetime.timedelta(hours=i)
        other[hour] = lb.fixed_kw(hour, budget) + (pump[hour] if hour in pump else 0.0)
    month_counted = pc.stored_counted(pc.read_state(CAP_STATE), pc.month_key(now))
    return {"other_kw": other, "month_counted": month_counted}


def hour_prices(influx, bucket: str, start: datetime.datetime) -> dict:
    """Hourly mean price_total (EUR/kWh) from start for HORIZON_H hours."""
    stop = start + datetime.timedelta(hours=HORIZON_H)
    flux = f"""
        from(bucket: "{bucket}")
          |> range(start: {start.strftime("%Y-%m-%dT%H:%M:%SZ")}, stop: {stop.strftime("%Y-%m-%dT%H:%M:%SZ")})
          |> filter(fn: (r) => r._measurement == "spot" and r._field == "price_total")
          |> aggregateWindow(every: 1h, fn: mean, createEmpty: false, timeSrc: "_start")
    """
    prices = {}
    for table in influx.query_with_retry(flux):
        for record in table.records:
            if record.get_value() is not None:
                prices[record.get_time().astimezone(UTC)] = float(record.get_value())
    return prices


def _staging(config) -> bool:
    flag = os.getenv("STAGING_MODE", "false").lower() in ("true", "1", "yes")
    return flag or config.influxdb_bucket_load_control.endswith("_staging")


def main() -> int:
    parser = argparse.ArgumentParser(description="Mail the car charging tip")
    parser.add_argument("--dry-run", action="store_true", help="Log the tip, mail nothing")
    args = parser.parse_args()
    config = get_config()
    settings = ct.load_tip_settings(config.car_tip_section)
    cap = pc.load_cap_settings(config.power_cap_section)
    budget = lb.load_budget_settings(config.load_budget_section)
    now = datetime.datetime.now(UTC)
    start = now.replace(minute=0, second=0, microsecond=0)
    try:
        prices = hour_prices(InfluxClient(config), config.influxdb_bucket_spotprice, start)
    except Exception as e:
        logger.error(f"Prices unavailable, no tip: {e}")
        return 1
    result, reason = ct.tip(prices, now, settings, cap, context(now, budget, cap))
    if result is None:
        logger.info(f"No tip today: {reason}")
        return 0
    body = ct.describe(result)
    logger.info(f"Car tip:\n{body}")
    if args.dry_run or _staging(config):
        return 0
    api_key = config.get("RESEND_API_KEY")
    to_email = config.get("ALERT_EMAIL_TO")
    if not api_key or not to_email:
        logger.error("Cannot mail the car tip: no email settings")
        return 1
    from_email = config.get("ALERT_EMAIL_FROM")
    kwargs = {"from_email": from_email} if from_email else {}
    subject = f"[RedHouse] Car tip: start at {ct.local_label(result[0].start)}"
    return 0 if send_alert_email(api_key, to_email, subject, body, **kwargs) else 1


if __name__ == "__main__":
    sys.exit(main())
