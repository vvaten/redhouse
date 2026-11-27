#!/usr/bin/env python
"""Hot water replays R1, R2, R3, R5 and R6 of design 10, section 5.

Reads the simulation dataset (scripts/sim/build_dataset.py) and the boost
tables of the price model; writes report.md and CSVs. No InfluxDB, no relay.

Usage: python scripts/sim/run_hot_water_sim.py [--data DIR] [--out DIR]
"""

import argparse
import datetime
import statistics
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_budget_sim as rbs  # noqa: E402

from src.common.local_time import HELSINKI  # noqa: E402
from src.control import hot_water_controller as hwc  # noqa: E402
from src.control import hot_water_detect as hd  # noqa: E402
from src.control import power_cap as pc  # noqa: E402
from src.monitoring import pump_health as ph  # noqa: E402
from src.simulation import hot_water_rule_sim as hrs  # noqa: E402
from src.simulation import hot_water_sim as hs  # noqa: E402

BOOST_TABLES = Path("E:/redhouse_data/price_model/boost_timing")
CONFIG = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


def rule_settings() -> dict:
    section = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["hot_water"]
    return hwc.load_settings(section, False)


def fmt(t: int) -> str:
    return pd.Timestamp(t, unit="s", tz="UTC").tz_convert(HELSINKI).strftime("%a %Y-%m-%d %H:%M")


def r1(frame, minutes, prices, program, events) -> tuple:
    def price(t):
        h = t - t % 3600
        return prices[h] if h in prices else None

    cycles = hd.find_cycles(minutes, minutes[-1].t + 3600, price)
    labelled = hs.label_history([hs.record(c) for c in cycles], hs.HISTORY_ANCHORS)
    cars = events[events["class"].str.startswith("car")]
    car = [(int(a.timestamp()), int(b.timestamp())) for a, b in zip(cars.index, pd.to_datetime(cars["end"], utc=True))]
    gates = hs.r1_gates(frame, cycles, labelled, car)
    kinds = {r.start: r for r in labelled}

    def is_on(t):
        h = t - t % 3600
        return h in program and program[h] == "ON"

    rows = []
    for c in cycles:
        r = kinds[c.start]
        lost = hd.lost_on_minutes(c, is_on, r.kind, 20)
        raw = sum(1 for t in range(c.start, c.end + 60, 60) if is_on(t))
        rows.append({"start": fmt(c.start), "minutes": c.minutes, "kind": r.kind, "unexpected": r.unexpected,
                     "source": c.source, "start_top": c.start_top, "peak_top": c.peak_top,
                     "kwh": round(c.kwh, 2), "eur": round(c.eur, 3), "lost_on_min": lost, "on_min_raw": raw})
    return pd.DataFrame(rows), gates, cycles, labelled


def r1_lines(table: pd.DataFrame, gates: dict) -> list:
    share = gates["matched"] / gates["reference"]
    boosts = table[table["kind"] == "legionella_boost"]
    regular = table[table["kind"] == "regular"]
    found = {t: [(r.kind, r.unexpected) for r in v] for t, v in gates["boosts"].items()}
    boost_ok = all(v and v[0] == ("legionella_boost", False) for v in found.values())
    pulses_ok = all("heater_run" in v for v in gates["pulses"].values())
    false_ok = all(not v for v in gates["false"].values()) and not gates["heater_in_car"]
    capped = int((regular["on_min_raw"] > 20).sum())
    lines = [
        "## R1 cycle log",
        "",
        f"- Reference runs (tank top rise): {gates['reference']}; found within 5 min: {gates['matched']} ({share:.1%}). Gate 95%: {'PASS' if share >= 0.95 else 'FAIL'}.",
        f"- Missed reference starts: {', '.join(fmt(t) for t in gates['missed']) or 'none'}.",
        f"- Known boosts as legionella_boost, expected: {'PASS' if boost_ok else 'FAIL'} {found}.",
        f"- Nov 14 sauna heater pulses as heater_run: {'PASS' if pulses_ok else 'FAIL'} {gates['pulses']}.",
        f"- Nov 17 00:02 and 05:02 stages, Nov 20 car frame, car sessions: no cycle: {'PASS' if false_ok else 'FAIL'}.",
        f"- Boost kWh median {boosts['kwh'].median():.2f} against 11.7 (06): {'PASS' if abs(boosts['kwh'].median() / 11.7 - 1) <= 0.15 else 'FAIL'}; each {list(boosts['kwh'])}.",
        f"- Regular run kWh median {regular['kwh'].median():.2f} (06: 1.13 emeters, 1.3 3EM; not gated).",
        f"- Regular lost ON minutes at most {regular['lost_on_min'].max()} (gate 22): the pump's 20 min hot water time caps it; {capped} of {len(regular)} runs had more raw ON minutes than 20.",
        f"- Cycles: {len(table)}; kinds {table['kind'].value_counts().to_dict()}.",
        "- The incomplete-boost check: see R2 (no 2026-02-13 frame in the minute data).",
    ]
    return lines


def r2_lines(cycles_csv: pd.DataFrame, labelled: list) -> list:
    lines = ["", "## R2 boost timer", ""]
    for case, expected, got in hs.r2_cases():
        lines.append(f"- {case}: expected {expected}, got {got}: {'PASS' if expected == got else 'FAIL'}")
    for day in ("2026-01-30", "2026-03-27"):
        for acts in (True, False):
            mailed, labels, nxt = hs.r2_missed(cycles_csv, day, acts)
            mode = "rule 2 act" if acts else "rule 2 not act"
            stalled = [fmt(r.start) for r in labels if r.stalled]
            when = "at the slot end" if acts else "at the slot end + 36 h"
            missed = f"for the expiry {fmt(mailed)}, {when}" if mailed else "none"
            lines.append(
                f"- {day} ({mode}): missed-boost mail {missed}; "
                f"stalled first cycle (incomplete mail): {stalled or 'none'}; next expiry {fmt(nxt)}."
            )
    last = labelled[-1].start + 4 * 3600
    incomplete = [fmt(r.start) for r in hs.hb.incomplete(labelled, last, lambda a, b: None)]
    lines.append(f"- Incomplete-boost mails on the minute data (5 boosts since 2026-10-23): {incomplete or 'none'} (gate: none).")
    return lines


def r3_lines(counts) -> list:
    return [
        "",
        "## R3 rule replay (decisions only, every rule in act)",
        "",
        f"- Minutes decided: {counts.minutes}; ON minutes by rule: {dict(counts.on)}; ON starts: {dict(counts.starts)}.",
        f"- ON between an expiry and its slot end by a rule other than 2 or 3: {counts.blocked_violations} (gate 0): {'PASS' if counts.blocked_violations == 0 else 'FAIL'}.",
        f"- Rule 7 ON in a dear hour: {counts.comfort_dear} (gate 0): {'PASS' if counts.comfort_dear == 0 else 'FAIL'}.",
        "- Rule 2 in program ON is allowed and counted in the boost_trigger minutes.",
    ]


def r5(hourly: pd.DataFrame, boosts: list) -> list:
    local = hourly.tz_convert(HELSINKI)
    base = local["emeter_avg"].clip(lower=0) / 1000
    off_kw, shared_kw = base.copy(), base.copy()
    stats = {"days": 0, "boost_days": 0, "slot_on_hours": 0, "count_changed": 0, "extra_max": 0.0}
    for _, day in local.groupby(local.index.date):
        if len(day) < 23 or day["price_total"].isna().any() or day["air_temperature"].isna().all():
            continue
        n = rbs.hours_to_heat(float(day["air_temperature"].mean()))
        month_start = pc.month_start(day.index[0].to_pydatetime())
        so_far = shared_kw[(shared_kw.index >= month_start) & (shared_kw.index < day.index[0])]
        cap_kw, _ = pc.month_cap(pc.counted_hours({t.to_pydatetime(): v for t, v in so_far.items()}, rbs.CAP), rbs.CAP)
        out = hrs.r5_day(day, n, pc.plan_limit_kw(cap_kw, rbs.CAP), rbs.CAP, rbs.BUDGET, boosts)
        stats["days"] += 1
        stats["boost_days"] += bool(out["slot"])
        stats["slot_on_hours"] += len(out["linked"] & out["slot"])
        stats["count_changed"] += len(out["linked"]) != len(out["off"])
        stats["extra_max"] = max(stats["extra_max"], max(out["extra_kw"]))
        for t in out["off"] - out["shared"]:
            shared_kw[t] -= rbs.BUDGET.pump_on_kw
        for t in out["shared"] - out["off"]:
            shared_kw[t] += rbs.BUDGET.pump_on_kw
    frame = pd.DataFrame({"old_kw": off_kw, "new_kw": shared_kw.clip(lower=0), "price": local["price_total"]})
    table = rbs.monthly(frame)
    rises = int((table["charge_change_eur"] > 0).sum())
    return [
        "",
        "## R5 budget replay (two winters, every recorded boost)",
        "",
        "- Both switches off: the programs are the budget's own pick (unit tests: the link returns the same frame).",
        f"- boost_budget: extra fixed load at most {stats['extra_max']:.1f} kW, only in boost hours; months where the power charge rose: {rises} (gate none): {'PASS' if rises == 0 else 'FAIL'}; charge change {table['charge_change_eur'].sum():.2f} EUR.",
        f"- planner_link: {stats['boost_days']} boost days; ON hours left in a boost slot: {stats['slot_on_hours']} (gate 0): {'PASS' if stats['slot_on_hours'] == 0 else 'FAIL'}; days whose hour count changed: {stats['count_changed']} (gate 0).",
        "- Lost minutes added once: unit tests (test_hot_water_planner).",
    ]


def r6_lines(minutes, behind, commands, outdoor) -> list:
    lines = ["", "## R6 pump health", ""]
    trip_from, trip_to = hs.local_epoch("2026-11-16 02:20"), hs.local_epoch("2026-11-16 04:45")
    for below in (5.0, 8.0):
        results = hs.health_replay(minutes, behind, commands, outdoor, ph.HealthSettings(90, below))
        alerts, last = [], None
        for t, v in results:
            if v.alarm and (last is None or t - last > 6 * 3600):
                alerts.append(t)
                last = t
        night = [t for t in alerts if trip_from <= t <= trip_to]
        weeks = (minutes[-1].t - minutes[0].t) / (7 * 86400)
        false = len(alerts) - len(night)
        lines.append(
            f"- below_outdoor_c {below}: alerts {[fmt(t) for t in alerts]}; alarm night by 03:55: "
            f"{'PASS' if night and min(night) <= hs.local_epoch('2026-11-16 03:55') else 'FAIL'}; "
            f"false {false} in {weeks:.1f} weeks (gate 1 a week): {'PASS' if false <= weeks else 'FAIL'}."
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default="E:/redhouse_data/sim/data_20261122")
    parser.add_argument("--out", default=f"E:/redhouse_data/sim/hot_water_{datetime.date.today():%Y%m%d}")
    args = parser.parse_args()
    data, out = Path(args.data), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    frame = hs.minute_frame(hs.load_csv(data / "tank.csv.gz"), hs.load_csv(data / "em3_minute.csv.gz"))
    frame = frame[frame.index >= "2026-10-15"]
    minutes = hs.minutes(frame)
    hourly = hs.load_csv(data / "hourly.csv.gz")
    commands = hs.load_csv(data / "pump_commands.csv.gz")
    events = hs.load_csv(Path("E:/redhouse_data/sim/data/events.csv"))
    prices = {int(t.timestamp()): float(v) for t, v in hourly["price_total"].dropna().items()}
    outdoor = {int(t.timestamp()): float(v) for t, v in hourly["air_temperature"].dropna().items()}
    program = hrs.program_hours(commands)
    table, gates, cycles, labelled = r1(frame, minutes, prices, program, events)
    table.to_csv(out / "cycles.csv", index=False)
    cycles_csv = hs.load_csv(BOOST_TABLES / "cycles.csv")
    boosts_csv = pd.read_csv(BOOST_TABLES / "boosts.csv")
    boosts = [int(pd.Timestamp(t).timestamp()) for t in boosts_csv["climb_start"]]
    counts = hrs.r3(minutes, cycles, labelled, prices, program, rule_settings())
    winters = pd.concat([hourly[(hourly.index >= f"{y}-11-01") & (hourly.index < f"{y + 1}-04-01")] for y in (2024, 2025)])
    lines = [f"# Hot water replays {datetime.date.today()}", "", f"Data: {data}, {fmt(minutes[0].t)} to {fmt(minutes[-1].t)}.", ""]
    lines += r1_lines(table, gates) + r2_lines(cycles_csv, labelled) + r3_lines(counts)
    lines += r5(winters, boosts) + r6_lines(minutes, hs.behind_minutes(hs.load_csv(data / "battery_minute.csv.gz")), commands, outdoor)
    regular = table[table["kind"] == "regular"]
    lines += ["", f"Regular runs per day: {len(regular) / ((minutes[-1].t - minutes[0].t) / 86400):.2f} (06: about 2.4); kWh median {statistics.median(regular['kwh']):.2f}."]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    used = {k: config[k] for k in ("hot_water", "pump_health", "load_budget")}
    (out / "config_used.yaml").write_text(yaml.safe_dump(used, allow_unicode=True), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
