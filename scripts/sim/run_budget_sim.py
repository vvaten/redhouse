#!/usr/bin/env python
"""Budget simulation 4.3 run 2 of design 09: the month's power charge with
and without the generator's power budget, on two winters of history.

Each day the pump's hours are selected from the hour prices as the
generator does (the heating curve on the day's mean outdoor temperature,
cheapest hours first), once without and once with the budget. The pump
load is then moved in the measured hourly import: import - on_old + on_new.

Usage: python scripts/sim/run_budget_sim.py
"""

import datetime
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.common.local_time import HELSINKI  # noqa: E402
from src.control import load_budget as lb  # noqa: E402
from src.control import power_cap as pc  # noqa: E402
from src.control.heating_optimizer import HeatingOptimizer  # noqa: E402

DATA = Path("E:/redhouse_data/sim/data")
CAP = pc.CapSettings(
    night_factor=0.8,
    night_start_hour=22,
    night_end_hour=7,
    month_floor_kw=7.5,
    plan_margin_kw=0.3,
    guard_margin_kw=0.1,
    tariff_eur_per_kw=1.97,
    max_import_kw=34.5,
)
BUDGET = lb.load_budget_settings(
    {
        "enabled": True,
        "base_kw": 1.6,
        "battery_allowance_kw": 1.5,
        "pump_on_kw": 3.4,
        "sauna_kw": 7.0,
        "sauna_share": 0.5,
        "sauna_weekdays": [5],
        "sauna_start_hour": 17,
        "sauna_end_hour": 22,
    }
)
CURVE_T = [-20.0, 0.0, 16.0]
CURVE_H = [12.0, 6.0, 2.0]
WINTERS = [(2024, 11), (2025, 11)]


def hours_to_heat(mean_t: float) -> float:
    hours = float(np.interp(mean_t, CURVE_T, CURVE_H, left=None, right=None))
    if mean_t < CURVE_T[0]:
        slope = (CURVE_H[1] - CURVE_H[0]) / (CURVE_T[1] - CURVE_T[0])
        hours = CURVE_H[0] + slope * (mean_t - CURVE_T[0])
    return round(max(hours, 0.0) * 4) / 4


def select(day: pd.DataFrame, n: float, limit_kw) -> set:
    priorities = pd.DataFrame({"heating_prio": day["price_total"] * BUDGET.pump_on_kw})
    if limit_kw is not None:
        priorities = lb.apply_budget(priorities, limit_kw, CAP, BUDGET)
    return set(HeatingOptimizer().select_cheapest_hours(priorities, n).index)


def run(hourly: pd.DataFrame) -> pd.DataFrame:
    local = hourly.tz_convert(HELSINKI)
    base = local["emeter_avg"].clip(lower=0) / 1000
    old_kw = base.copy()
    new_kw = base.copy()
    moved = 0
    for _, day in local.groupby(local.index.date):
        if len(day) < 23 or day["price_total"].isna().any() or day["air_temperature"].isna().all():
            continue
        n = hours_to_heat(float(day["air_temperature"].mean()))
        month_start = pc.month_start(day.index[0].to_pydatetime())
        so_far = new_kw[(new_kw.index >= month_start) & (new_kw.index < day.index[0])]
        counted = pc.counted_hours({t.to_pydatetime(): v for t, v in so_far.items()}, CAP)
        cap_kw, _ = pc.month_cap(counted, CAP)
        old = select(day, n, None)
        new = select(day, n, pc.plan_limit_kw(cap_kw, CAP))
        for t in old - new:
            new_kw[t] -= BUDGET.pump_on_kw
        for t in new - old:
            new_kw[t] += BUDGET.pump_on_kw
        moved += len(new - old)
    new_kw = new_kw.clip(lower=0.0)
    frame = pd.DataFrame({"old_kw": old_kw, "new_kw": new_kw, "price": local["price_total"]})
    frame.attrs["moved"] = moved
    return frame


def monthly(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for month, part in frame.groupby(frame.index.strftime("%Y-%m")):
        result = {"month": month}
        for col in ("old_kw", "new_kw"):
            counted = pc.counted_hours({t.to_pydatetime(): v for t, v in part[col].items()}, CAP)
            cap_kw, _ = pc.month_cap(counted, CAP)
            result[f"cap_{col}"] = round(cap_kw, 2)
            result[f"energy_eur_{col}"] = round(float((part[col] * part["price"]).sum()), 2)
        result["charge_change_eur"] = round((result["cap_new_kw"] - result["cap_old_kw"]) * 1.97, 2)
        rows.append(result)
    return pd.DataFrame(rows)


def main():
    hourly = pd.read_csv(DATA / "hourly.csv.gz", index_col=0)
    hourly.index = pd.to_datetime(hourly.index, utc=True, format="ISO8601")
    parts = []
    for year, month in WINTERS:
        start = pd.Timestamp(datetime.datetime(year, month, 1), tz="UTC")
        stop = pd.Timestamp(datetime.datetime(year + 1, 4, 1), tz="UTC")
        parts.append(hourly[(hourly.index >= start) & (hourly.index < stop)])
    frame = run(pd.concat(parts))
    table = monthly(frame)
    out = Path(f"E:/redhouse_data/sim/budget_{datetime.date.today():%Y%m%d}")
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "months.csv", index=False)
    rises = table[table["charge_change_eur"] > 0]
    lines = [
        f"# Budget replay {datetime.date.today()}",
        "",
        "The pump load moved in the measured import; prices and outdoor as recorded.",
        "Limit: the old pump hours are the replay's own cheapest-hours pick, not the",
        "hours the pump really ran (that history is not in the dataset).",
        f"Pump hours moved by the budget: {frame.attrs['moved']}.",
        f"Power charge change over all months: {table['charge_change_eur'].sum():.2f} EUR.",
        f"Energy cost change: {(table['energy_eur_new_kw'] - table['energy_eur_old_kw']).sum():.2f} EUR.",
        f"Months where the charge rose: {len(rises)} (gate: none).",
        "",
        "```",
        table.to_string(index=False),
        "```",
    ]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
