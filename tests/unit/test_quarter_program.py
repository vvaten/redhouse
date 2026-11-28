"""The quarter program pieces of design 12: window, runs, split, re-plan."""

import datetime

import pandas as pd
import pytest

from src.common.local_time import HELSINKI
from src.control import quarter_program as qprog

DAY = HELSINKI.localize(datetime.datetime(2026, 12, 2))
DAY_S = int(DAY.timestamp())
Q = 900


def _on(t: int, minutes: int, executed: bool = False) -> dict:
    entry = {"timestamp": t, "command": "ON", "duration_minutes": minutes}
    if executed:
        entry["executed_at"] = t
    return entry


def _ale(t: int) -> dict:
    return {"timestamp": t, "command": "ALE", "duration_minutes": None}


def _program(entries: list) -> dict:
    return {"program_date": "2026-12-01", "loads": {"geothermal_pump": {"schedule": entries}}}


def test_on_quarters_and_the_last_on_end():
    entries = [_on(DAY_S - 8 * Q, 60), _ale(DAY_S - 4 * Q), _on(DAY_S - 2 * Q, 15)]
    assert qprog.on_quarters(entries, DAY_S - 6 * Q, DAY_S) == {
        DAY_S - 6 * Q,
        DAY_S - 5 * Q,
        DAY_S - 2 * Q,
    }
    assert qprog.last_on_end(entries, DAY_S) == DAY_S - Q
    assert qprog.last_on_end(entries, DAY_S - 7 * Q) == DAY_S - 7 * Q
    assert qprog.lead_off(DAY_S - 4 * Q, DAY_S) == 4 and qprog.lead_off(None, DAY_S) == 0


def test_an_entry_without_a_duration_is_refused():
    with pytest.raises(ValueError):
        qprog.on_quarters([{"timestamp": 0, "command": "ON"}], 0, Q)


def test_a_live_evening_run_plans_the_rest_of_today_too():
    """At 16:06 the window starts at 16:15; today's ON from 22:00 is carried."""
    before = _program([_on(DAY_S - 2 * 3600, 60), _ale(DAY_S - 3600)])
    now = DAY_S - 8 * 3600 + 6 * 60
    w = qprog.plan_window(DAY_S, DAY_S + 86400, before, "geothermal_pump", now)
    assert w.start == DAY_S - 8 * 3600 + Q and w.carried == 4 and w.today is before


def test_the_window_knows_the_on_quarters_and_evu_before_it():
    """16:06: ON from 16:00 cut at 16:15; EVU 14:00 to 18:00 in another plan."""
    start = DAY_S - 8 * 3600
    on_before = _program([_on(start, 60), _ale(start + 3600)])
    w = qprog.plan_window(DAY_S, DAY_S + 86400, on_before, "geothermal_pump", start + 360)
    assert w.lead_on == 1 and w.evu_from is None
    evu = {"timestamp": start - 2 * 3600, "command": "EVU", "duration_minutes": 240}
    w2 = qprog.plan_window(DAY_S, DAY_S + 86400, _program([evu]), "geothermal_pump", start + 360)
    assert w2.evu_from == start - 2 * 3600 and w2.lead_on == 0


def test_a_run_of_touching_evu_entries_counts_from_its_first():
    """EVU 14:00-16:15 cut by a re-plan, then 16:15-18:00: one block from 14:00."""
    t = DAY_S - 10 * 3600
    entries = [
        {"timestamp": t, "command": "EVU", "duration_minutes": 135},
        {"timestamp": t + 135 * 60, "command": "EVU", "duration_minutes": 105},
    ]
    assert qprog.running_evu_start(entries, t + 3 * 3600) == t
    assert qprog.running_evu_start(entries, t + 5 * 3600) is None


def test_revise_today_refuses_a_program_without_the_load():
    with pytest.raises(ValueError):
        qprog.revise_today({"loads": {}}, "geothermal_pump", [], DAY_S)


def test_without_a_live_run_the_window_is_the_day():
    before = _program([_on(DAY_S - 2 * 3600, 60), _ale(DAY_S - 3600)])
    w = qprog.plan_window(DAY_S, DAY_S + 86400, before, "geothermal_pump", None)
    assert (w.start, w.carried, w.lead, w.today) == (DAY_S, 0, 4, None)
    alone = qprog.plan_window(DAY_S, DAY_S + 86400, None, "geothermal_pump", DAY_S - 3600)
    assert (alone.start, alone.lead) == (DAY_S, 0)


def _priorities(prices: list, start=DAY) -> pd.DataFrame:
    index = pd.date_range(pd.Timestamp(start), periods=len(prices), freq="15min")
    return pd.DataFrame(
        {
            "heating_prio": [p * 3.0 for p in prices],
            "price_total": prices,
            "solar_yield_avg_prediction": [0.0] * len(prices),
        },
        index=index,
    )


def test_runs_merge_consecutive_quarters():
    pr = _priorities([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    runs = qprog.runs(pr.iloc[[0, 1, 2, 4]])
    assert list(runs["duration_minutes"]) == [45, 15]
    assert runs["cost_eur"].iloc[0] == pytest.approx((0.1 + 0.2 + 0.3) * 3.0 * 0.25)
    assert runs["price_total"].iloc[0] == pytest.approx(0.2)
    assert qprog.runs(pr.iloc[[]]).empty


def test_select_keeps_the_gap_and_reports_its_cost():
    prices = [0.05] * 8 + [0.30] * 80 + [0.05] * 8
    pr = _priorities(prices)
    caps = {qprog.hour_key(t): 4 for t in pr.index}
    sel = qprog.select(pr, 8, caps, 8.0, 0)
    assert sel.gap_kept and sel.cost_eur > sel.free_cost_eur
    assert len(sel.on_index) == 8 and sel.runs["duration_minutes"].sum() == 120


def test_split_at_cuts_a_block_over_the_boundary():
    entries = [_on(DAY_S - Q, 30), _ale(DAY_S + Q)]
    entries[0]["estimated_cost_eur"] = 0.2
    before, after = qprog.split_at(entries, DAY_S)
    assert before == [dict(entries[0], duration_minutes=15, estimated_cost_eur=0.1)]
    assert after[0]["timestamp"] == DAY_S and after[0]["duration_minutes"] == 15
    assert after[0]["local_time"].startswith("2026-12-02T00:00")
    assert after[1] == entries[1]


def test_revise_today_keeps_the_past_and_replaces_the_rest():
    start = DAY_S - 8 * 3600
    old = [
        _on(start - 3600, 30, executed=True),
        _ale(start - 1800),
        _on(start + 3600, 60),
        _ale(start + 7200),
    ]
    new = [_on(start + 2 * Q, 15), _ale(start + 3 * Q)]
    revised = qprog.revise_today(_program(old), "geothermal_pump", new, start)
    load = revised["loads"]["geothermal_pump"]
    assert load["schedule"] == old[:2] + new
    assert load["total_hours_on"] == pytest.approx(0.75) and "revised_at" in revised


def test_revise_today_ends_a_block_running_over_the_start():
    start = DAY_S - 8 * 3600
    old = [_on(start - Q, 60, executed=True), _ale(start + 3 * Q)]
    revised = qprog.revise_today(_program(old), "geothermal_pump", [_on(start + 4 * Q, 15)], start)
    schedule = revised["loads"]["geothermal_pump"]["schedule"]
    assert schedule[0]["duration_minutes"] == 15
    assert schedule[1]["command"] == "ALE" and schedule[1]["timestamp"] == start
    same = qprog.revise_today(_program(old), "geothermal_pump", [_on(start, 30)], start)
    assert [e["command"] for e in same["loads"]["geothermal_pump"]["schedule"]] == ["ON", "ON"]
