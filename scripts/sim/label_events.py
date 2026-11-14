#!/usr/bin/env python
"""Find candidate load events in the 3EM minute data (design 09, 7.4).

A step is a rise of at least STEP_KW on a phase, held for HOLD_MIN minutes,
measured as the median after minus the median before. Each event is
classified by its per-phase step, length, local time, tank top and pump
state. Writes E:/redhouse_data/sim/data/events.csv with source "auto".

Usage: python scripts/sim/label_events.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DATA = Path("E:/redhouse_data/sim/data")
STEP_KW = 0.8
HOLD_MIN = 3
BEFORE_MIN = 5
PHASES = ["phase1_power", "phase2_power", "phase3_power"]
TANK_TOP = "K\u00e4ytt\u00f6vesi ylh"


def load(name):
    df = pd.read_csv(DATA / f"{name}.csv.gz", index_col=0)
    df.index = pd.to_datetime(df.index, utc=True, format="ISO8601")
    return df


def minute_grid(em3):
    """Per-phase kW on a 1-minute grid (the points carry write times)."""
    kw = em3[PHASES].div(1000).resample("1min").mean()
    return kw.interpolate(limit=3)


def steps(kw):
    """Per-phase step at each minute: median of the next HOLD_MIN minus the
    median of the BEFORE_MIN before."""
    after = kw[::-1].rolling(HOLD_MIN, min_periods=HOLD_MIN).median()[::-1]
    before = kw.shift(1).rolling(BEFORE_MIN, min_periods=BEFORE_MIN).median()
    return after - before


def events(kw, step):
    """Rising steps and the time each load fell back, one row per event."""
    total = step.sum(axis=1)
    rising = (step.max(axis=1) >= STEP_KW) & (total == total.rolling(5, center=True).max())
    rows = []
    for t in step.index[rising]:
        s = step.loc[t]
        base = kw.loc[:t].iloc[-BEFORE_MIN - 1 : -1].median()
        stepped = [p for p in PHASES if s[p] >= STEP_KW * 0.6]
        level = base[stepped].sum() + s[stepped].sum() * 0.5
        later = kw.loc[t:, stepped].sum(axis=1)
        below = later[later < level]
        end = below.index[0] if len(below) else kw.index[-1]
        rows.append(
            {
                "start": t,
                "end": end,
                "minutes": (end - t).total_seconds() / 60,
                **{f"step_l{i + 1}": round(float(s[p]), 2) for i, p in enumerate(PHASES)},
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    # One event per load: drop starts inside an earlier event of the same phases
    keep, last_end = [], None
    for _, row in out.iterrows():
        if last_end is not None and row["start"] < last_end:
            continue
        keep.append(row)
        last_end = row["end"]
    return pd.DataFrame(keep)


def pump_state_at(pump, t):
    before = pump[pump.index <= t]
    return before["command"].iloc[-1] if len(before) else "unknown"


def classify(row, tank, pump):
    s = np.array([row["step_l1"], row["step_l2"], row["step_l3"]])
    on = s >= STEP_KW * 0.6
    local = row["start"].tz_convert("Europe/Helsinki")
    window = tank.loc[row["start"] : row["end"]][TANK_TOP] if TANK_TOP in tank else pd.Series()
    tank_rise = float(window.max() - window.iloc[0]) if len(window) > 1 else 0.0
    pump_cmd = pump_state_at(pump, row["start"])
    if on.sum() == 1:
        phase = int(np.argmax(on)) + 1
        if 0.9 <= s[on][0] <= 1.25 and phase in (1, 3):
            return f"garage_or_1kw_l{phase}", tank_rise, pump_cmd
        return f"single_l{phase}", tank_rise, pump_cmd
    if on.sum() == 3:
        mean = float(s.mean())
        if 0.7 <= mean <= 1.4:
            return "compressor", tank_rise, pump_cmd
        if 3.2 <= mean <= 4.2 and row["minutes"] >= 20:
            return "car11", tank_rise, pump_cmd
        if 1.6 <= mean <= 2.0 and row["minutes"] >= 30 and tank_rise < 3:
            return "car5", tank_rise, pump_cmd
        if 1.8 <= mean <= 2.4 and tank_rise >= 5:
            return "boost", tank_rise, pump_cmd
        if 1.9 <= mean <= 2.3 and pump_cmd == "ALE":
            return "direct_heater?", tank_rise, pump_cmd
        if 2.2 <= mean <= 3.0 and 16 <= local.hour <= 23:
            return "sauna?", tank_rise, pump_cmd
        return "balanced_other", tank_rise, pump_cmd
    return "multi_phase_other", tank_rise, pump_cmd


def main():
    em3 = load("em3_minute")
    tank = load("tank").resample("1min").mean()
    pump = load("pump_commands")
    kw = minute_grid(em3)
    found = events(kw, steps(kw))
    if found.empty:
        print("No events")
        return
    labels = [classify(row, tank, pump) for _, row in found.iterrows()]
    found["class"] = [c for c, _, _ in labels]
    found["tank_rise_k"] = [round(r, 1) for _, r, _ in labels]
    found["pump"] = [p for _, _, p in labels]
    found["local_start"] = (
        found["start"].dt.tz_convert("Europe/Helsinki").dt.strftime("%a %Y-%m-%d %H:%M")
    )
    found["source"] = "auto"
    big = found[found["minutes"] >= 5]
    big.to_csv(DATA / "events.csv", index=False)
    print(big["class"].value_counts().to_string())
    cols = [
        "local_start",
        "minutes",
        "step_l1",
        "step_l2",
        "step_l3",
        "class",
        "tank_rise_k",
        "pump",
    ]
    print(big[big["class"] != "multi_phase_other"][cols].to_string(index=False))


if __name__ == "__main__":
    main()
