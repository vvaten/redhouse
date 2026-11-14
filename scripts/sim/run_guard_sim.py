#!/usr/bin/env python
"""Load guard simulation 4.4 of design 09: replay the guard minute by
minute over the recorded 3EM data, with the cap as it stood each hour.

Usage: python scripts/sim/run_guard_sim.py
"""

import datetime
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.control import load_guard as lg  # noqa: E402
from src.control import power_cap as pc  # noqa: E402
from src.simulation import guard_sim as gsim  # noqa: E402

DATA = Path("E:/redhouse_data/sim/data")
SETTINGS = pc.CapSettings(
    night_factor=0.8,
    night_start_hour=22,
    night_end_hour=7,
    month_floor_kw=7.5,
    plan_margin_kw=0.3,
    guard_margin_kw=0.1,
    tariff_eur_per_kw=1.97,
    max_import_kw=34.5,
)
GUARD = lg.GuardSettings(garage_kw=2.13, pump_ale_kw=3.0, pump_evu_kw=1.0)


def load(name):
    df = pd.read_csv(DATA / f"{name}.csv.gz", index_col=0)
    df.index = pd.to_datetime(df.index, utc=True, format="ISO8601")
    return df


def main():
    em3 = load("em3_minute")
    pump = load("pump_commands")
    hourly = load("hourly")
    import_kw = {
        t.to_pydatetime(): max(float(v), 0.0) / 1000
        for t, v in hourly["emeter_avg"].dropna().items()
    }
    counted = pc.counted_hours(import_kw, SETTINGS)
    hours = gsim.replay(gsim.samples_from_minutes(em3), counted, pump, SETTINGS, GUARD)
    table = gsim.summarize(hours, counted)
    out = Path(f"E:/redhouse_data/sim/guard_{datetime.date.today():%Y%m%d}")
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "hours.csv", index=False)
    over = table[table["over"]]
    acted = table[table["action_min"] >= 0]
    false_alarms = acted[~acted["over"]]
    saved = over[(over["action_min"] >= 0) & (over["after_shed_kw"] <= over["limit_kw"])]
    if table.empty:
        print("No hours to replay")
        return
    days = max((table["hour"].max() - table["hour"].min()).total_seconds() / 86400, 1.0)
    lines = [
        f"# Load guard replay {datetime.date.today()}",
        "",
        f"Hours replayed: {len(table)} over {days:.1f} days.",
        "",
        "Limits of the replay: 'after_shed_kw' assumes the first sheds run at",
        "their nominal kW for the rest of the hour and ignores later escalation,",
        "so the saved count is an optimistic bound. ALE can still run the 6.4 kW",
        "resistor, and EVU, which stops it, is counted at 1 kW only. The live",
        "guard misses the rename minutes (:09 :24 :39 :54); the replay does not.",
        "",
        f"Hours that ended over the guard limit: {len(over)}.",
        f"  of them the guard acted in: {int((over['action_min'] >= 0).sum())}",
        f"  of them shedding would have kept under the limit: {len(saved)}",
        f"Actions in hours that ended under the limit: {len(false_alarms)}"
        f" ({len(false_alarms) / days * 7:.1f} a week).",
        "",
        "## Hours over the limit",
        "```",
        over.to_string(index=False),
        "```",
        "",
        "## Actions in hours that ended under the limit",
        "```",
        false_alarms.head(40).to_string(index=False),
        "```",
    ]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:12]))


if __name__ == "__main__":
    main()
