"""Offline replay of the load guard over recorded 3EM minute data."""

import bisect
import datetime
from collections import deque
from dataclasses import dataclass

import pandas as pd

from src.control import load_guard as lg
from src.control import power_cap as pc

WINDOW_MIN = 70
CAP_UPDATE_MIN = 17
PHASES = ["phase1_power", "phase2_power", "phase3_power"]


@dataclass
class HourRecord:
    start: datetime.datetime
    factor: float
    limit_kw: float
    first_action_min: int = -1
    first_action_kw: float = 0.0
    shed_kw: float = 0.0
    labels: tuple = ()
    pump: str = "unknown"


def samples_from_minutes(em3: pd.DataFrame) -> list:
    rows = em3[PHASES + ["net_total_energy", "total_energy"]].dropna()
    return [
        lg.Sample(
            t.timestamp(),
            (r["phase1_power"] / 1000, r["phase2_power"] / 1000, r["phase3_power"] / 1000),
            float(r["net_total_energy"]),
            float(r["total_energy"]),
        )
        for t, r in rows.iterrows()
    ]


class PumpTimeline:
    """The pump command in force at any time, from the executed commands."""

    def __init__(self, pump: pd.DataFrame):
        ordered = pump.sort_index()
        self.times = [t.timestamp() for t in ordered.index]
        self.commands = [str(c) for c in ordered["command"]]

    def at(self, t: float) -> str:
        i = bisect.bisect_right(self.times, t)
        return self.commands[i - 1] if i else "unknown"


def cap_before(
    counted: dict, hour: datetime.datetime, minute: int, settings: pc.CapSettings
) -> float:
    """The cap the live guard sees: earlier hours of the month, without the
    hour just ended until the :17 cap update has counted it."""
    month = pc.month_key(hour)
    last = hour if minute >= CAP_UPDATE_MIN else hour - datetime.timedelta(hours=1)
    earlier = {t: v for t, v in counted.items() if pc.month_key(t) == month and t < last}
    cap_kw, _ = pc.month_cap(earlier, settings)
    return cap_kw


def replay(
    samples: list,
    counted: dict,
    pump: pd.DataFrame,
    settings: pc.CapSettings,
    guard: lg.GuardSettings,
) -> dict:
    """{hour start: HourRecord} for every hour with samples."""
    timeline = PumpTimeline(pump)
    window: deque = deque()
    hours: dict = {}
    caps: dict = {}
    for sample in samples:
        window.append(sample)
        while window and window[0].t < sample.t - WINDOW_MIN * 60:
            window.popleft()
        start = lg.utc(lg.hour_start(sample.t))
        if start not in hours:
            hours[start] = HourRecord(
                start,
                pc.factor(start, settings),
                0.0,
                pump=timeline.at(sample.t),
            )
        rec = hours[start]
        minute = int((sample.t - start.timestamp()) // 60)
        key = (start, minute >= CAP_UPDATE_MIN)
        if key not in caps:
            caps[key] = pc.guard_limit_kw(cap_before(counted, start, minute, settings), settings)
        rec.limit_kw = caps[key]
        available = lg.available_sheds(guard, False, timeline.at(sample.t))
        verdict = lg.judge(list(window), sample.t, rec.factor, rec.limit_kw, available)
        if verdict is None:
            continue
        if verdict.step_class and verdict.step_class not in rec.labels:
            rec.labels = rec.labels + (verdict.step_class,)
        if verdict.sheds and rec.first_action_min < 0:
            rec.first_action_min = int((sample.t - start.timestamp()) // 60)
            rec.first_action_kw = verdict.counted_kw
            rest_h = (start.timestamp() + 3600 - sample.t) / 3600
            rec.shed_kw = sum(s.kw for s in verdict.sheds) * rest_h * rec.factor
    return hours


def summarize(hours: dict, counted: dict) -> pd.DataFrame:
    rows = []
    for start, rec in sorted(hours.items()):
        if start not in counted:
            continue
        end_kw = counted[start]
        rows.append(
            {
                "hour": start,
                "local": start.astimezone(pc.HELSINKI).strftime("%a %m-%d %H:%M"),
                "counted_kw": round(end_kw, 2),
                "limit_kw": round(rec.limit_kw, 2),
                "over": end_kw > rec.limit_kw,
                "action_min": rec.first_action_min,
                "action_kw": round(rec.first_action_kw, 2),
                "after_shed_kw": (
                    round(end_kw - rec.shed_kw, 2) if rec.first_action_min >= 0 else None
                ),
                "pump": rec.pump,
                "labels": ",".join(rec.labels),
            }
        )
    return pd.DataFrame(rows)
