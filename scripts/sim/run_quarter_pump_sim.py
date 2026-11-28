#!/usr/bin/env python
"""Quarter pump program replay (design 12): what quarter planning saves
over today's whole hours, and what each comfort level (longest gap
without heating) costs, on the quarter prices since 2025-10-01.

Each day's need comes from the heating curve on the day's real mean
outdoor temperature. The rolling plan runs as live: at 16:00 the rest of
today and all of tomorrow are planned together, the tail counts as a gap.
All plans are costed at the real quarter prices, pump 3 kW, no solar.

Usage: python scripts/sim/run_quarter_pump_sim.py [--curve config|design01]
"""

import argparse
import datetime
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.control import quarter_plan as qp  # noqa: E402
from src.control.heating_curve import HeatingCurve  # noqa: E402

DATA = Path("E:/redhouse_data/sim/data")
CONFIG = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
TZ = "Europe/Helsinki"
PUMP_KW = 3.0
FIRST = datetime.date(2025, 10, 1)
END = datetime.date(2026, 4, 1)
PLAN_HOUR = 16
LEVELS = (None, 12, 8, 6, 4)
DESIGN01 = {-20.0: 16.0, -10.0: 12.0, 0.0: 8.0, 10.0: 4.0, 16.0: 2.0}


def load_prices() -> pd.Series:
    spot = pd.read_csv(DATA / "spot.csv.gz", index_col=0)
    spot.index = pd.to_datetime(spot.index, utc=True, format="ISO8601")
    prices = spot["price_total"].dropna()
    return prices[prices.index >= pd.Timestamp("2025-09-30T21:00:00Z")]


def daily_outdoor() -> pd.Series:
    hourly = pd.read_csv(DATA / "hourly.csv.gz", index_col=0)
    hourly.index = pd.to_datetime(hourly.index, utc=True, format="ISO8601")
    temp = hourly["air_temperature"].dropna()
    return temp.groupby(temp.index.tz_convert(TZ).date).mean()


def curve(name: str) -> HeatingCurve:
    if name == "design01":
        return HeatingCurve(DESIGN01)
    points = yaml.safe_load(CONFIG.read_text())["heating"]["curve"]
    return HeatingCurve({float(k): float(v) for k, v in points.items()})


def day_start(day: datetime.date) -> pd.Timestamp:
    return pd.Timestamp(datetime.datetime.combine(day, datetime.time()), tz=TZ).tz_convert("UTC")


def quarters(start: pd.Timestamp, stop: pd.Timestamp) -> pd.DatetimeIndex:
    return pd.date_range(start, stop, freq="15min", inclusive="left")


def need(day: datetime.date, temps: pd.Series, heating: HeatingCurve) -> int:
    return round(heating.calculate_heating_hours(float(temps[day])) * 4)


def hourly_plan(day: datetime.date, prices: pd.Series, n: int) -> set:
    """Today's planner: the cheapest whole hours on the hour mean; a part
    hour runs its first quarters, as the executor starts it on the hour."""
    qs = quarters(day_start(day), day_start(day + datetime.timedelta(days=1)))
    means = prices.reindex(qs).groupby(qs.floor("h")).mean().sort_values()
    full, rest = divmod(n, 4)
    picked = list(means.index[: full + (1 if rest else 0)])
    on = set()
    for i, hour in enumerate(picked):
        count = rest if (rest and i == len(picked) - 1) else 4
        on.update(hour + pd.Timedelta(minutes=15 * k) for k in range(count))
    return on


def rolling(days: list, prices: pd.Series, temps: pd.Series, heating, max_off) -> set:
    """ON quarters of the rolling plan: at 16:00 on D-1 plan the rest of D-1
    with D, the rest's planned ON count pooled with D's need."""
    on: set = set()
    gap = None if max_off is None else max_off * 4
    for day in days:
        plan_at = day_start(day) - pd.Timedelta(hours=24 - PLAN_HOUR)
        qs = quarters(plan_at, day_start(day + datetime.timedelta(days=1)))
        rest = {t for t in on if t >= plan_at}
        before = [t for t in on if t < plan_at]
        lead = 0 if not before else int((plan_at - max(before)) / pd.Timedelta(minutes=15)) - 1
        on -= rest
        costs = list(prices.reindex(qs).ffill().bfill() * PUMP_KW * 0.25)
        hours = [int(t.floor("h").timestamp()) for t in qs]
        caps = {h: 4 for h in hours}
        result = qp.plan(costs, hours, caps, len(rest) + need(day, temps, heating), gap, lead)
        on.update(t for t, x in zip(qs, result.on) if x)
    return on


def stats(on: set, prices: pd.Series, days: list) -> dict:
    qs = quarters(day_start(days[0]), day_start(days[-1] + datetime.timedelta(days=1)))
    flags = [t in on for t in qs]
    cost = float(sum(prices.reindex([t for t in qs if t in on]).fillna(0)) * PUMP_KW * 0.25)
    per_day = {}
    for day in days:
        day_qs = quarters(day_start(day), day_start(day + datetime.timedelta(days=1)))
        per_day[day] = qp.longest_off([t in on for t in day_qs])
    starts = sum(1 for a, b in zip([False] + flags, flags) if b and not a)
    return {
        "cost_eur": round(cost, 2),
        "eur_per_day": round(cost / len(days), 3),
        "longest_gap_h": qp.longest_off(flags) / 4,
        "days_gap_over_8h": sum(1 for g in per_day.values() if g > 32),
        "on_starts_per_day": round(starts / len(days), 2),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--curve", choices=("config", "design01"), default="config")
    args = parser.parse_args()
    prices, temps, heating = load_prices(), daily_outdoor(), curve(args.curve)
    days = [d for d in pd.date_range(FIRST, END, inclusive="left").date if d in temps.index]
    rows = []
    hourly: set = set()
    for day in days:
        hourly |= hourly_plan(day, prices, need(day, temps, heating))
    rows.append({"plan": "whole hours (today)", **stats(hourly, prices, days)})
    for level in LEVELS:
        name = "quarters, no gap limit" if level is None else f"quarters, gap <= {level} h"
        rows.append(
            {"plan": name, **stats(rolling(days, prices, temps, heating, level), prices, days)}
        )
    table = pd.DataFrame(rows)
    out = Path(f"E:/redhouse_data/sim/quarter_pump_{datetime.date.today():%Y%m%d}")
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / f"levels_{args.curve}.csv", index=False)
    text = f"# Quarter pump replay, curve {args.curve}, {FIRST} to {END}, {len(days)} days\n\n"
    text += "```\n" + table.to_string(index=False) + "\n```\n"
    (out / f"report_{args.curve}.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
