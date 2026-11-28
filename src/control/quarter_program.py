"""The pump program in quarters (design 12).

The window to plan, its ON quarters, the runs they form, and the rest of
today's program re-planned together with tomorrow's. Schedule entries are
dicts with an epoch "timestamp", a "command" and "duration_minutes".
"""

import copy
import datetime
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from src.common.local_time import HELSINKI
from src.control import quarter_plan as qp

QUARTER_S = 15 * 60
QUARTER_H = 0.25


@dataclass(frozen=True)
class Selection:
    runs: pd.DataFrame
    on_index: pd.DatetimeIndex
    cost_eur: float
    free_cost_eur: float
    gap_kept: bool
    quarters: int


@dataclass(frozen=True)
class Window:
    """Epoch [start, stop) to plan; carried: ON quarters of the day before
    that the window takes over; today: that day's program when re-planned."""

    start: int
    stop: int
    carried: int
    lead: int
    today: Optional[dict]


def next_quarter(now: int) -> int:
    return now - now % QUARTER_S + QUARTER_S


def plan_window(day_start: int, day_stop: int, before: Optional[dict], load_id: str, now) -> Window:
    """The window for a day's program. With now (a live run) and the day
    before's program, the rest of that day is planned too."""
    entries = []
    if before is not None and load_id in before["loads"]:
        entries = before["loads"][load_id]["schedule"]
    if now is not None and before is not None and next_quarter(now) < day_start:
        start = next_quarter(now)
        carried = len(on_quarters(entries, start, day_start))
        lead = lead_off(last_on_end(entries, start), start)
        return Window(start, day_stop, carried, lead, before)
    return Window(
        day_start, day_stop, 0, lead_off(last_on_end(entries, day_start), day_start), None
    )


def _minutes(entry: dict) -> int:
    if "duration_minutes" not in entry or entry["duration_minutes"] is None:
        raise ValueError(f"Schedule entry at {entry['timestamp']} has no duration")
    return int(entry["duration_minutes"])


def on_quarters(entries: list, start: int, stop: int) -> set:
    """Quarter starts in [start, stop) that the ON entries cover."""
    out: set = set()
    for e in entries:
        if e["command"] != "ON":
            continue
        t = int(e["timestamp"])
        end = t + _minutes(e) * 60
        first = t - t % QUARTER_S + (QUARTER_S if t % QUARTER_S else 0)
        out.update(q for q in range(first, end, QUARTER_S) if start <= q < stop)
    return out


def last_on_end(entries: list, before: int) -> Optional[int]:
    """When the last ON before the time ended (cut at the time), or None."""
    ends = [
        min(int(e["timestamp"]) + _minutes(e) * 60, before)
        for e in entries
        if e["command"] == "ON" and int(e["timestamp"]) < before
    ]
    return max(ends) if ends else None


def lead_off(last_end: Optional[int], start: int) -> int:
    """OFF quarters between the last ON and the window; 0 when unknown."""
    return 0 if last_end is None else max(0, (start - last_end) // QUARTER_S)


def hour_key(t: pd.Timestamp) -> pd.Timestamp:
    return t.tz_convert("UTC").floor("h").tz_convert(HELSINKI)


def select(priorities: pd.DataFrame, n: int, caps: dict, max_off_h, lead: int) -> Selection:
    """The window's ON quarters. priorities: one row per quarter, indexed by
    aware quarter start, heating_prio in EUR per hour of ON. caps: {hour
    start: ON quarters allowed}, keyed as hour_key gives."""
    costs = list(priorities["heating_prio"] * QUARTER_H)
    hours = [hour_key(t) for t in priorities.index]
    max_off = None if max_off_h is None else int(round(max_off_h * 4))
    chosen = qp.plan(costs, hours, caps, n, max_off, lead)
    free = qp.plan(costs, hours, caps, n, None, lead) if max_off is not None else chosen
    flags = pd.Series(chosen.on, index=priorities.index)
    return Selection(
        runs=runs(priorities.loc[flags.to_numpy()]),
        on_index=priorities.index[flags.to_numpy()],
        cost_eur=_real_cost(chosen.on, costs),
        free_cost_eur=_real_cost(free.on, costs),
        gap_kept=chosen.gap_kept,
        quarters=n,
    )


def _real_cost(on: tuple, costs: list) -> float:
    return float(sum(c for c, x in zip(costs, on) if x))


def runs(selected: pd.DataFrame) -> pd.DataFrame:
    """Consecutive ON quarters merged: one row per run, indexed by its start,
    with duration_minutes, cost_eur and the run's mean price and rate."""
    rows = []
    for _, run in selected.groupby(_run_ids(selected.index)):
        rows.append(
            {
                "start": run.index[0],
                "duration_minutes": 15 * len(run),
                "heating_prio": float(run["heating_prio"].mean()),
                "price_total": float(run["price_total"].mean()),
                "solar_yield_avg_prediction": float(run["solar_yield_avg_prediction"].mean()),
                "cost_eur": float(run["heating_prio"].sum() * QUARTER_H),
            }
        )
    columns = ["duration_minutes", "heating_prio", "price_total"]
    columns += ["solar_yield_avg_prediction", "cost_eur"]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows).set_index("start")[columns]


def _run_ids(index: pd.Index) -> list:
    ids, run, previous = [], 0, None
    for t in index:
        if previous is not None and (t - previous) != pd.Timedelta(seconds=QUARTER_S):
            run += 1
        ids.append(run)
        previous = t
    return ids


def _stamp(entry: dict, t: int) -> dict:
    out = dict(entry)
    when = datetime.datetime.fromtimestamp(t, HELSINKI)
    out["timestamp"] = t
    out["utc_time"] = when.astimezone(datetime.timezone.utc).isoformat()
    out["local_time"] = when.isoformat()
    return out


def split_at(entries: list, boundary: int) -> tuple:
    """(entries before, entries from) the boundary; an ON or EVU entry that
    runs over it is cut in two."""
    before, after = [], []
    for e in entries:
        t = int(e["timestamp"])
        if t >= boundary:
            after.append(e)
            continue
        if e["command"] in ("ON", "EVU") and t + _minutes(e) * 60 > boundary:
            head = dict(e, duration_minutes=(boundary - t) // 60)
            tail = _stamp(dict(e, duration_minutes=_minutes(e) - (boundary - t) // 60), boundary)
            if "estimated_cost_eur" in e and e["estimated_cost_eur"]:
                share = head["duration_minutes"] / _minutes(e)
                head["estimated_cost_eur"] = round(e["estimated_cost_eur"] * share, 3)
                tail["estimated_cost_eur"] = round(e["estimated_cost_eur"] * (1 - share), 3)
            before.append(head)
            after.append(tail)
        else:
            before.append(e)
    return before, after


def revise_today(program: dict, load_id: str, new_entries: list, start: int) -> dict:
    """Today's program with its schedule from start replaced by new_entries.
    Entries before start stay, executed or not; a block over start is cut."""
    revised = copy.deepcopy(program)
    load = revised["loads"][load_id]
    kept, _ = split_at(load["schedule"], start)
    cut = [e for e in kept if e["command"] in ("ON", "EVU")]
    running = cut and int(cut[-1]["timestamp"]) + _minutes(cut[-1]) * 60 == start
    if running and not any(int(e["timestamp"]) == start for e in new_entries):
        ale = {"command": "ALE", "duration_minutes": None, "reason": "replanned"}
        new_entries = [_stamp(ale, start)] + new_entries
    revised["loads"][load_id] = with_schedule(load, kept + new_entries)
    revised["revised_at"] = datetime.datetime.now(HELSINKI).isoformat()
    return revised


def with_schedule(load: dict, entries: list) -> dict:
    """The load with the schedule and its ON totals set from the entries."""
    on = [e for e in entries if e["command"] == "ON"]
    costs = [e["estimated_cost_eur"] for e in on if "estimated_cost_eur" in e]
    return dict(
        load,
        schedule=entries,
        total_hours_on=sum(_minutes(e) for e in on) / 60,
        total_intervals_on=len(on),
        estimated_cost_eur=round(sum(c for c in costs if c), 2),
    )
