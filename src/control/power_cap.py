"""The month's power charge base: the third-highest counted hourly import.

Helen bills the third-highest hourly mean import of the month. Hours
from 22 to 07 local count at 80 %. An hour is named by its start.
"""

import datetime
import json
import math
from dataclasses import dataclass, fields
from pathlib import Path

from src.common.atomic_json import write_json
from src.common.local_time import HELSINKI

TOP_N = 3


@dataclass(frozen=True)
class CapSettings:
    night_factor: float
    night_start_hour: int
    night_end_hour: int
    month_floor_kw: float
    plan_margin_kw: float
    guard_margin_kw: float
    tariff_eur_per_kw: float
    # Over this a counter step is a glitch, under its negative a meter reset
    max_import_kw: float


def load_cap_settings(section: dict) -> CapSettings:
    names = [f.name for f in fields(CapSettings)]
    missing = [k for k in names if k not in section]
    if missing:
        raise ValueError(f"config.yaml power_cap section lacks {missing}")
    values: dict = {}
    for name in names:
        if name.endswith("_hour"):
            if not isinstance(section[name], int):
                raise ValueError(f"power_cap {name} must be a whole hour")
            values[name] = section[name]
        else:
            values[name] = float(section[name])
    return CapSettings(**values)


def factor(hour_start: datetime.datetime, settings: CapSettings) -> float:
    """The counting factor of the hour that starts at hour_start (aware)."""
    hour = hour_start.astimezone(HELSINKI).hour
    night = hour >= settings.night_start_hour or hour < settings.night_end_hour
    return settings.night_factor if night else 1.0


def month_key(t: datetime.datetime) -> str:
    return t.astimezone(HELSINKI).strftime("%Y-%m")


def hourly_import_kw(first_wh: dict, settings: CapSettings) -> dict:
    """Mean import kW per hour from the counter's first value in each hour.

    Hour H is first[H+1] - first[H]. A net export hour imports 0. A step
    beyond max_import_kw either way (a meter reset or a glitch) gives no value."""
    hours = sorted(first_wh)
    result = {}
    for cur, nxt in zip(hours, hours[1:]):
        if nxt - cur != datetime.timedelta(hours=1):
            continue
        kw = (first_wh[nxt] - first_wh[cur]) / 1000.0
        if abs(kw) > settings.max_import_kw:
            continue
        result[cur] = max(kw, 0.0)
    return result


def counted_hours(import_kw: dict, settings: CapSettings) -> dict:
    return {t: kw * factor(t, settings) for t, kw in import_kw.items()}


def month_cap(counted: dict, settings: CapSettings) -> tuple:
    """(cap kW, top hours as [(hour start, counted kW)]): the third highest,
    never under the month floor."""
    top = sorted(counted.items(), key=lambda item: item[1], reverse=True)[:TOP_N]
    third = top[TOP_N - 1][1] if len(top) == TOP_N else 0.0
    return max(third, settings.month_floor_kw), top


def plan_limit_kw(cap_kw: float, settings: CapSettings) -> float:
    return cap_kw - settings.plan_margin_kw


def guard_limit_kw(cap_kw: float, settings: CapSettings) -> float:
    return cap_kw - settings.guard_margin_kw


def state_dict(month: str, cap_kw: float, top: list, counted: dict, now: float) -> dict:
    return {
        "month": month,
        "updated": now,
        "cap_kw": cap_kw,
        "top": [[t.isoformat(), kw] for t, kw in top],
        "counted_kw": {t.isoformat(): kw for t, kw in sorted(counted.items())},
    }


def write_state(path: Path, state: object) -> None:
    write_json(path, state)


def read_state(path: Path) -> dict:
    """The state file as a dict; missing or unreadable gives {}."""
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def stored_counted(state: dict, month: str) -> dict:
    """The counted hours of the state, if it is for this month."""
    if "month" not in state or state["month"] != month or "counted_kw" not in state:
        return {}
    stored = state["counted_kw"]
    if not isinstance(stored, dict):
        return {}
    result = {}
    for key, kw in stored.items():
        try:
            value = float(kw)
            hour = datetime.datetime.fromisoformat(key)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            result[hour] = value
    return result


def read_cap_kw(path: Path, now: datetime.datetime, settings: CapSettings) -> float:
    """The cap of this month from the state file. A missing, broken or older
    month's file gives the floor: the cap only grows inside a month."""
    state = read_state(path)
    if "month" not in state or state["month"] != month_key(now) or "cap_kw" not in state:
        return settings.month_floor_kw
    try:
        cap = float(state["cap_kw"])
    except (TypeError, ValueError):
        return settings.month_floor_kw
    if not math.isfinite(cap):
        return settings.month_floor_kw
    return max(cap, settings.month_floor_kw)


def month_start(now: datetime.datetime) -> datetime.datetime:
    local = now.astimezone(HELSINKI)
    first = HELSINKI.localize(datetime.datetime(local.year, local.month, 1))
    return first.astimezone(datetime.timezone.utc)


def in_month(hours: dict, month: str, now: datetime.datetime) -> dict:
    """Hours of the given local month that have ended by now."""
    end = now - datetime.timedelta(hours=1)
    return {t: v for t, v in hours.items() if month_key(t) == month and t <= end}


def missing_hours(counted: dict, now: datetime.datetime) -> list:
    """Completed hours of this month without a value, oldest first."""
    missing = []
    t = month_start(now)
    while t <= now - datetime.timedelta(hours=1):
        if t not in counted:
            missing.append(t)
        t += datetime.timedelta(hours=1)
    return missing
