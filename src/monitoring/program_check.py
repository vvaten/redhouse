#!/usr/bin/env python
"""Check that tomorrow's heating program exists, and mail if it does not.

Runs once a day at 18:00 local. Generation runs at 16:06, so this
leaves nearly two hours of slack before it complains, and the whole
evening to act before the day it covers begins.
"""

import datetime
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytz

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger
from src.monitoring.email_sender import send_alert_email
from src.monitoring.health_check import _is_staging

logger = setup_logger(__name__, "program_check.log")

# Local, not UTC: "tomorrow" is a local day and Finland has DST, so a
# fixed offset is right for half the year only.
HELSINKI = pytz.timezone("Europe/Helsinki")

DEFAULT_PROGRAM_DIR = "."
JOURNAL_LINES = 12
JOURNAL_TIMEOUT_S = 30


def program_path(program_date: datetime.date, base_dir: str) -> Path:
    """Where the executor will look for that day's program.

    Mirrors program_executor.load_program, the only consumer that
    matters: the file decides whether execution works.
    """
    name = f"heating_program_schedule_{program_date.isoformat()}.json"
    return Path(base_dir) / program_date.strftime("%Y-%m") / name


def local_day_bounds(program_date: datetime.date) -> tuple[str, str]:
    """RFC3339 bounds of that local day, each with its own DST offset.

    Localize both midnights separately. Adding 24 h to the start keeps
    the start offset, which loses an hour in October and gains one in
    March, because a local day is then 25 or 23 hours long.
    """
    next_day = program_date + datetime.timedelta(days=1)
    start = HELSINKI.localize(datetime.datetime.combine(program_date, datetime.time.min))
    stop = HELSINKI.localize(datetime.datetime.combine(next_day, datetime.time.min))
    return start.isoformat(), stop.isoformat()


def load_control_points(influx: InfluxClient, program_date: datetime.date) -> Optional[int]:
    """Count load_control points dated within that local day.

    Context for the mail only. The bucket can fail its write while the
    file is fine, and the reverse, so it never decides the verdict.
    """
    start, stop = local_day_bounds(program_date)
    query = f"""
from(bucket: "{influx.config.influxdb_bucket_load_control}")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._field == "is_on")
  |> group()
  |> count()
"""
    try:
        tables = influx.query_with_retry(query)
    except Exception as e:
        logger.warning("Could not count load_control points: %s", e)
        return None

    for table in tables:
        for record in table.records:
            return int(record.get_value())
    return 0


def generator_unit() -> str:
    """The generator service for this install. Staging carries a prefix."""
    name = "redhouse-generate-program"
    if _is_staging():
        name = name.replace("redhouse-", "redhouse-staging-", 1)
    return f"{name}.service"


def generate_service_result() -> str:
    """The last lines of the generator's journal, for the mail body."""
    try:
        result = subprocess.run(
            [
                "journalctl",
                "-u",
                generator_unit(),
                "-n",
                str(JOURNAL_LINES),
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=JOURNAL_TIMEOUT_S,
        )
        return result.stdout.strip() or "(no journal output)"
    except Exception as e:
        return f"(could not read journal: {e})"


def build_body(program_date: datetime.date, path: Path, points: Optional[int]) -> str:
    """Arrive with the evidence, not just the verdict."""
    counted = "unknown" if points is None else str(points)
    return "\n".join(
        [
            f"No heating program for {program_date.isoformat()}.",
            "",
            f"Expected file: {path}",
            f"load_control points for that day: {counted}",
            "",
            "Generation runs at 16:06 daily.",
            "The executor has NO fallback: with no file it cannot run.",
            "",
            f"Last lines of {generator_unit()}:",
            generate_service_result(),
        ]
    )


def tomorrow_local(now: Optional[datetime.datetime] = None) -> datetime.date:
    """Tomorrow's local date."""
    if now is None:
        now = datetime.datetime.now(HELSINKI)
    return (now + datetime.timedelta(days=1)).date()


def check_program_exists(program_date: datetime.date, base_dir: str) -> bool:
    """Whether that day's program file is present."""
    path = program_path(program_date, base_dir)
    if path.is_file():
        logger.info("Heating program for %s present at %s", program_date.isoformat(), path)
        return True

    logger.warning("Heating program for %s MISSING, expected %s", program_date.isoformat(), path)
    return False


def main() -> int:
    """Entry point.

    A missing program exits 0, because the mail is the signal and a
    failed unit would only add noise. Failing to SEND that mail exits
    1: then the job itself did not happen and nothing else says so.
    """
    config = get_config()
    base_dir = config.get("PROGRAM_OUTPUT_DIR", DEFAULT_PROGRAM_DIR)
    program_date = tomorrow_local()

    if check_program_exists(program_date, base_dir):
        return 0

    api_key = config.get("RESEND_API_KEY")
    to_email = config.get("ALERT_EMAIL_TO")
    from_email = config.get("ALERT_EMAIL_FROM", "RedHouse <alerts@resend.dev>")

    if not api_key or not to_email:
        logger.error("Email not configured, cannot report the missing program")
        return 1

    points = None
    try:
        points = load_control_points(InfluxClient(config), program_date)
    except Exception as e:
        logger.warning("Could not reach InfluxDB for context: %s", e)

    sent = send_alert_email(
        api_key=api_key,
        to_email=to_email,
        subject=f"[RedHouse WARNING] No heating program for {program_date.isoformat()}",
        body=build_body(program_date, program_path(program_date, base_dir), points),
        from_email=from_email,
    )
    if not sent:
        logger.error("Could not send the missing program mail")
        return 1

    logger.info("Missing program mail sent for %s", program_date.isoformat())
    return 0


if __name__ == "__main__":
    sys.exit(main())
