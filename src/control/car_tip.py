"""The evening car charging tip: the cheapest window before the deadline.

Pure functions. A window costs its energy plus any rise in the month's
power charge. A tip goes out only when the best window saves at least
min_saving_eur against starting at naive_start_hour.
"""

import datetime
import math
from dataclasses import dataclass, fields

from src.common.local_time import HELSINKI
from src.control.power_cap import CapSettings, factor, month_cap


@dataclass(frozen=True)
class TipSettings:
    session_kwh: float
    kw_low: float
    kw_high: float
    deadline_hour: int
    naive_start_hour: int
    min_saving_eur: float


def load_tip_settings(section: dict) -> TipSettings:
    names = [f.name for f in fields(TipSettings)]
    missing = [k for k in names if k not in section]
    if missing:
        raise ValueError(f"config.yaml car_tip section lacks {missing}")
    return TipSettings(
        session_kwh=float(section["session_kwh"]),
        kw_low=float(section["kw_low"]),
        kw_high=float(section["kw_high"]),
        deadline_hour=int(section["deadline_hour"]),
        naive_start_hour=int(section["naive_start_hour"]),
        min_saving_eur=float(section["min_saving_eur"]),
    )


@dataclass(frozen=True)
class Window:
    start: datetime.datetime
    kw: float
    energy_eur: float
    # Counted kW of each hour with the car and the other planned load
    counted: dict
    charge_rise_eur: float = 0.0

    @property
    def total_eur(self) -> float:
        return self.energy_eur + self.charge_rise_eur

    @property
    def peak_kw(self) -> float:
        return max(self.counted.values())


def _hour_loads(kw: float, kwh: float) -> list:
    """The car's kWh in each hour of a session that starts on the hour."""
    full = int(kwh // kw)
    rest = kwh - full * kw
    return [kw] * full + ([rest] if rest > 1e-9 else [])


def charge_rise_kw(window_counted: dict, month_counted: dict, cap: CapSettings) -> float:
    """How much the month's base (the third-highest hour) grows with the window."""
    before, _ = month_cap(month_counted, cap)
    merged = dict(month_counted)
    for hour, kw in window_counted.items():
        if hour not in merged or kw > merged[hour]:
            merged[hour] = kw
    after, _ = month_cap(merged, cap)
    return after - before


def window_at(prices: dict, start, kw: float, s: TipSettings, cap: CapSettings, ctx: dict):
    """The session from start, or None if a needed hour has no price.
    ctx: other_kw {hour: kW}, month_counted {hour: counted kW}."""
    counted = {}
    energy = 0.0
    for i, kwh in enumerate(_hour_loads(kw, s.session_kwh)):
        hour = start + datetime.timedelta(hours=i)
        if hour not in prices or hour not in ctx["other_kw"]:
            return None
        energy += prices[hour] * kwh
        counted[hour] = (ctx["other_kw"][hour] + kwh) * factor(hour, cap)
    rise = charge_rise_kw(counted, ctx["month_counted"], cap) * cap.tariff_eur_per_kw
    return Window(start, kw, energy, counted, rise)


def candidates(now: datetime.datetime, kw: float, s: TipSettings) -> list:
    """Every whole-hour start from the next hour that still ends by the deadline."""
    local = now.astimezone(HELSINKI)
    first = HELSINKI.normalize(
        local.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
    )
    deadline_day = first.date() + datetime.timedelta(days=0 if first.hour < s.deadline_hour else 1)
    deadline = HELSINKI.localize(
        datetime.datetime.combine(deadline_day, datetime.time(s.deadline_hour))
    )
    hours = math.ceil(s.session_kwh / kw)
    starts = []
    t = first.astimezone(datetime.timezone.utc)
    while t + datetime.timedelta(hours=hours) <= deadline.astimezone(datetime.timezone.utc):
        starts.append(t)
        t += datetime.timedelta(hours=1)
    return starts


def best(prices: dict, now, kw: float, s: TipSettings, cap: CapSettings, ctx: dict):
    windows = [window_at(prices, t, kw, s, cap, ctx) for t in candidates(now, kw, s)]
    priced = [w for w in windows if w is not None]
    return min(priced, key=lambda w: (w.total_eur, w.start)) if priced else None


def naive(prices: dict, now, kw: float, s: TipSettings, cap: CapSettings, ctx: dict):
    """The session started at naive_start_hour tonight, the habit to beat."""
    local = now.astimezone(HELSINKI)
    start = HELSINKI.localize(
        datetime.datetime.combine(local.date(), datetime.time(s.naive_start_hour))
    )
    return window_at(prices, start.astimezone(datetime.timezone.utc), kw, s, cap, ctx)


def tip(prices: dict, now, s: TipSettings, cap: CapSettings, ctx: dict):
    """(best low-power window, habit window, best high-power window) when the
    saving, power charge included, is worth a mail; else (None, reason)."""
    low = best(prices, now, s.kw_low, s, cap, ctx)
    habit = naive(prices, now, s.kw_low, s, cap, ctx)
    if low is None or habit is None:
        return None, "prices or the planned load are missing"
    if habit.total_eur - low.total_eur < s.min_saving_eur:
        return None, f"saving {habit.total_eur - low.total_eur:.2f} EUR is under the threshold"
    return (low, habit, best(prices, now, s.kw_high, s, cap, ctx)), ""


def describe(result: tuple) -> str:
    low, habit, high = result
    lines = [
        f"Start the car at {local_label(low.start)} at {low.kw:.1f} kW: "
        f"about {low.energy_eur:.2f} EUR of energy.",
        f"Starting at {local_label(habit.start)} would cost {habit.total_eur:.2f} EUR "
        f"({habit.total_eur - low.total_eur:.2f} EUR more, power charge included).",
    ]
    if low.charge_rise_eur > 0:
        lines.append(
            f"The window raises the month's power charge by {low.charge_rise_eur:.2f} EUR."
        )
    if high is not None:
        lines.append(
            f"At {high.kw:.1f} kW the best start is {local_label(high.start)}: "
            f"{high.total_eur:.2f} EUR in all, a peak of {high.peak_kw:.1f} kW counted."
        )
    return "\n".join(lines)


def local_label(t: datetime.datetime) -> str:
    return t.astimezone(HELSINKI).strftime("%a %H:%M")
