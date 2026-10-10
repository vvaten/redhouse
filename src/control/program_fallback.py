#!/usr/bin/env python
"""Copy a heating program forward when a day has none.

Runs at 23:50 local, after the generator's last hourly retry at 23:06.
"""

import copy
import datetime
import json
import os
from pathlib import Path
from typing import Optional

import pytz

from src.common.config import get_config
from src.common.logger import setup_logger
from src.monitoring.email_sender import send_alert_email
from src.monitoring.program_check import (
    HELSINKI,
    PROGRAM_BASE_DIR,
    generate_service_result,
    program_path,
)

logger = setup_logger(__name__, "program_fallback.log")

FALLBACK_KEY = "fallback"

# Tomorrow is judged only after the 23:06 retry has had time to finish.
# Today is repaired at any hour: nothing else makes today's program.
TOMORROW_FROM = datetime.time(23, 30)


def target_dates(now: datetime.datetime) -> list:
    """The days that must have a program by now, earliest first."""
    local = now.astimezone(HELSINKI)
    days = [local.date()]
    if local.time() >= TOMORROW_FROM:
        days.append(local.date() + datetime.timedelta(days=1))
    return days


def is_fallback_copy(program: dict) -> bool:
    """Whether the program was copied by this module, not generated."""
    return FALLBACK_KEY in program


def load_valid_program(path: Path) -> Optional[dict]:
    """The program at path, or None if it is missing or the executor
    could not use it: unreadable, not JSON, or without version and loads."""
    if not path.is_file():
        return None
    try:
        with open(path) as f:
            program = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning("Program file %s is unreadable: %s", path, e)
        return None
    if (
        not isinstance(program, dict)
        or "version" not in program
        or not isinstance(program.get("loads"), dict)
    ):
        logger.warning("Program file %s lacks version or loads", path)
        return None
    return program


def _move_one_day(local: datetime.datetime) -> datetime.datetime:
    """The same wall-clock time one calendar day later. In the repeated
    October hour take the first; a missing March hour moves to 04:xx."""
    naive = local.replace(tzinfo=None) + datetime.timedelta(days=1)
    try:
        return HELSINKI.localize(naive, is_dst=None)
    except pytz.exceptions.AmbiguousTimeError:
        return HELSINKI.localize(naive, is_dst=True)
    except pytz.exceptions.NonExistentTimeError:
        return HELSINKI.normalize(HELSINKI.localize(naive, is_dst=False))


def _belongs_to(entry: dict, day: datetime.date) -> bool:
    """On that local day, or the ALE at the next midnight that ends it.
    Leaves out the previous day's entries merged in at midnight."""
    local = datetime.datetime.fromisoformat(entry["local_time"]).astimezone(HELSINKI)
    if local.date() == day:
        return True
    return local.date() == day + datetime.timedelta(days=1) and local.time() == datetime.time.min


def _shift_entry(entry: dict) -> dict:
    local = datetime.datetime.fromisoformat(entry["local_time"]).astimezone(HELSINKI)
    moved = _move_one_day(local)
    shifted = {k: v for k, v in entry.items() if k not in ("executed_at", "execution_result")}
    shifted["timestamp"] = int(moved.timestamp())
    shifted["local_time"] = moved.isoformat()
    shifted["utc_time"] = moved.astimezone(datetime.timezone.utc).isoformat()
    return shifted


def copy_program_forward(program: dict, new_date: datetime.date, now: datetime.datetime) -> dict:
    """A copy of the program for new_date, with nothing marked executed.
    Two entries that land on one instant, on either DST day, keep the
    later one."""
    source_day = datetime.date.fromisoformat(program["program_date"])
    copied = copy.deepcopy(program)
    copied["program_date"] = new_date.isoformat()
    for load in copied["loads"].values():
        by_time = {}
        for entry in load["schedule"]:
            if _belongs_to(entry, source_day):
                shifted = _shift_entry(entry)
                by_time[shifted["timestamp"]] = shifted
        load["schedule"] = [by_time[t] for t in sorted(by_time)]

    previous = program[FALLBACK_KEY]["days_in_a_row"] if is_fallback_copy(program) else 0
    copied[FALLBACK_KEY] = {
        "copied_from": program["program_date"],
        "copied_at": now.astimezone(HELSINKI).isoformat(),
        "days_in_a_row": previous + 1,
    }
    if "execution_status" in copied:
        copied["execution_status"]["executed_intervals"] = 0
        copied["execution_status"]["last_executed_timestamp"] = None
        copied["execution_status"]["next_execution_timestamp"] = None
    return copied


def write_program(program: dict, path: Path) -> None:
    """Write atomically, so the executor never reads half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(program, f, indent=2)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _mail(config, subject: str, body: str) -> bool:
    api_key = config.get("RESEND_API_KEY")
    to_email = config.get("ALERT_EMAIL_TO")
    if not api_key or not to_email:
        logger.error("Email not configured, cannot report: %s", subject)
        return False
    from_email = config.get("ALERT_EMAIL_FROM")
    if from_email:
        return send_alert_email(api_key, to_email, subject, body, from_email=from_email)
    return send_alert_email(api_key, to_email, subject, body)


def _journal_tail() -> list:
    return ["", "Last lines of the generator journal:", generate_service_result()]


def _copied_body(copied: dict, path: Path) -> str:
    fallback = copied[FALLBACK_KEY]
    lines = [
        f"No usable heating program was generated for {copied['program_date']}.",
        f"The executor will run a copy of {fallback['copied_from']} instead,",
        "with every command moved forward one day.",
        "",
        f"Copied days in a row: {fallback['days_in_a_row']}",
        f"File: {path}",
        "Grafana shows no plan for this day: the copy is not written to load_control.",
    ]
    return "\n".join(lines + _journal_tail())


def _no_copy_body(day: datetime.date, reason: str) -> str:
    lines = [
        f"No heating program for {day.isoformat()}, and no copy could be made:",
        reason,
        "The executor cannot run. The pump stays in its last state.",
    ]
    return "\n".join(lines + _journal_tail())


def _critical(config, day: datetime.date, reason: str) -> int:
    logger.error("No program for %s: %s", day.isoformat(), reason)
    sent = _mail(
        config,
        f"[RedHouse CRITICAL] No heating program for {day.isoformat()}, no copy made",
        _no_copy_body(day, reason),
    )
    return 0 if sent else 1


def ensure_day(day: datetime.date, now: datetime.datetime, base_dir: str, config) -> int:
    """Copy the previous day's program to day unless day has a usable one.
    Exits as program_check does: 0 when a mail went out or none was
    needed, 1 when a needed mail could not be sent."""
    path = program_path(day, base_dir)
    if load_valid_program(path) is not None:
        logger.info("Heating program for %s present at %s", day.isoformat(), path)
        return 0

    source = program_path(day - datetime.timedelta(days=1), base_dir)
    program = load_valid_program(source)
    if program is None:
        return _critical(config, day, f"{source} is missing or unusable too.")

    try:
        copied = copy_program_forward(program, day, now)
        write_program(copied, path)
    except (OSError, ValueError, KeyError, TypeError) as e:
        return _critical(config, day, f"Copying {source} failed: {e}")
    logger.warning("Copied %s to %s as the fallback program", source, path)

    sent = _mail(
        config,
        f"[RedHouse WARNING] Running a copied heating program for {day.isoformat()}",
        _copied_body(copied, path),
    )
    return 0 if sent else 1


def ensure_program(now: datetime.datetime, base_dir: str, config) -> int:
    """Make sure every day that needs a program by now has one."""
    status = 0
    for day in target_dates(now):
        status |= ensure_day(day, now, base_dir, config)
    return status


def main() -> int:
    """Entry point."""
    return ensure_program(datetime.datetime.now(HELSINKI), PROGRAM_BASE_DIR, get_config())
