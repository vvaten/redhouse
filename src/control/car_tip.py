"""The evening car charging tip: the cheapest window before the deadline.

Pure functions. A window starts on a quarter and costs each quarter's
energy at that quarter's price, plus any rise in the month's power charge,
which counts whole hours. A tip goes out only when the best window saves
at least min_saving_eur against charging at once from plug_in_hour.
"""

import datetime
import math
from dataclasses import dataclass, fields

from src.common.local_time import HELSINKI
from src.control.power_cap import CapSettings, factor, month_cap

QUARTER = datetime.timedelta(minutes=15)
MISSING = "prices or the planned load are missing"


@dataclass(frozen=True)
class TipSettings:
    session_kwh: float
    kw_low: float
    kw_high: float
    deadline_hour: int
    plug_in_hour: int
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
        plug_in_hour=int(section["plug_in_hour"]),
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


def _quarter_loads(kw: float, kwh: float) -> list:
    """The car's kWh in each quarter of a session that starts on a quarter."""
    full = _quarter_count(kw, kwh) - 1
    return [kw / 4] * full + [kwh - full * kw / 4]


def _quarter_count(kw: float, kwh: float) -> int:
    return max(1, math.ceil(kwh / (kw / 4) - 1e-9))


def floor_hour(t: datetime.datetime) -> datetime.datetime:
    return t.replace(minute=0, second=0, microsecond=0)


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
    """The session from start, or None if a needed quarter has no price.
    prices: {quarter start: EUR/kWh}. ctx: other_kw {hour: kW},
    month_counted {hour: counted kW}."""
    car_kwh: dict = {}
    energy = 0.0
    for i, kwh in enumerate(_quarter_loads(kw, s.session_kwh)):
        quarter = start + i * QUARTER
        hour = floor_hour(quarter)
        if quarter not in prices or hour not in ctx["other_kw"]:
            return None
        energy += prices[quarter] * kwh
        car_kwh[hour] = (car_kwh[hour] if hour in car_kwh else 0.0) + kwh
    counted = {h: (ctx["other_kw"][h] + e) * factor(h, cap) for h, e in car_kwh.items()}
    rise = charge_rise_kw(counted, ctx["month_counted"], cap) * cap.tariff_eur_per_kw
    return Window(start, kw, energy, counted, rise)


def _next_quarter(now: datetime.datetime) -> datetime.datetime:
    """The next quarter start in UTC; Helsinki keeps whole-hour offsets."""
    utc = now.astimezone(datetime.timezone.utc).replace(second=0, microsecond=0)
    return utc.replace(minute=utc.minute - utc.minute % 15) + QUARTER


def plug_in_start(now: datetime.datetime, s: TipSettings) -> datetime.datetime:
    """Today's plug_in_hour in UTC."""
    local = now.astimezone(HELSINKI)
    start = HELSINKI.localize(
        datetime.datetime.combine(local.date(), datetime.time(s.plug_in_hour))
    )
    return start.astimezone(datetime.timezone.utc)


def candidates(now: datetime.datetime, kw: float, s: TipSettings) -> list:
    """Every quarter start from the next quarter that still ends by the deadline."""
    first = _next_quarter(now)
    local = first.astimezone(HELSINKI)
    deadline_day = local.date() + datetime.timedelta(days=0 if local.hour < s.deadline_hour else 1)
    deadline = HELSINKI.localize(
        datetime.datetime.combine(deadline_day, datetime.time(s.deadline_hour))
    ).astimezone(datetime.timezone.utc)
    length = _quarter_count(kw, s.session_kwh) * QUARTER
    starts = []
    t = first
    while t + length <= deadline:
        starts.append(t)
        t += QUARTER
    return starts


def _starts(now, kw: float, s: TipSettings) -> list:
    earliest = plug_in_start(now, s)
    return [t for t in candidates(now, kw, s) if t >= earliest]


def best(prices: dict, now, kw: float, s: TipSettings, cap: CapSettings, ctx: dict):
    """The cheapest window that starts at or after the plug-in."""
    windows = [window_at(prices, t, kw, s, cap, ctx) for t in _starts(now, kw, s)]
    priced = [w for w in windows if w is not None]
    return min(priced, key=lambda w: (w.total_eur, w.start)) if priced else None


def plug_in_window(prices: dict, now, kw: float, s: TipSettings, cap: CapSettings, ctx: dict):
    """The session that starts when the car is plugged in tonight."""
    return window_at(prices, plug_in_start(now, s), kw, s, cap, ctx)


def tip(prices: dict, now, s: TipSettings, cap: CapSettings, ctx: dict):
    """(best low-power window, plug-in window, best high-power window) when the
    saving, power charge included, is worth a mail; else (None, reason)."""
    if plug_in_start(now, s) < _next_quarter(now):
        return None, "the run is after the plug-in hour"
    if not _starts(now, s.kw_low, s):
        return None, "no session from the plug-in ends by the deadline"
    low = best(prices, now, s.kw_low, s, cap, ctx)
    plug_in = plug_in_window(prices, now, s.kw_low, s, cap, ctx)
    if low is None or plug_in is None:
        return None, MISSING
    saving = plug_in.total_eur - low.total_eur
    if saving < s.min_saving_eur:
        return None, f"saving {saving:.2f} EUR is under the threshold"
    return (low, plug_in, best(prices, now, s.kw_high, s, cap, ctx)), ""


def describe(result: tuple) -> str:
    low, plug_in, high = result
    lines = [
        f"Start the car at {local_label(low.start)} at {low.kw:.1f} kW: "
        f"about {low.total_eur:.2f} EUR, power charge included.",
        f"Charging at once from {local_label(plug_in.start)} would cost "
        f"{plug_in.total_eur:.2f} EUR ({plug_in.total_eur - low.total_eur:.2f} EUR more).",
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
