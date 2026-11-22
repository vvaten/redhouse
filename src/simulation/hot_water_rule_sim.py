"""Rule replay R3 and budget replay R5 of design 10, section 5.

R3 decides every minute with every rule in act on the recorded tank, prices
and program; it only counts decisions, the relay effect is unknown until the
owner's test 3. R5 replays the day plan with the boost in it.
"""

import bisect
from collections import Counter
from dataclasses import dataclass

import pandas as pd

from src.control import hot_water_boost as hb
from src.control import hot_water_rules as hr
from src.control.heating_optimizer import HeatingOptimizer
from src.control.hot_water_planner import BoostBudget, slot_last
from src.control.load_budget import BudgetSettings, apply_budget, fixed_kw
from src.simulation import hot_water_sim as hs

ALL_ACT = {r: "act" for r in hr.RULES}


@dataclass
class RuleCounts:
    minutes: int
    on: Counter
    starts: Counter
    blocked_violations: int
    comfort_dear: int


def program_hours(commands: pd.DataFrame) -> dict:
    """{hour start: command one minute into the hour} from executed commands."""
    rows = sorted((int(t.timestamp()), str(c)) for t, c in zip(commands.index, commands["command"]))
    times = [t for t, _ in rows]
    out = {}
    first, last = rows[0][0] - rows[0][0] % 3600, rows[-1][0] + 86400
    for h in range(first, last, 3600):
        i = bisect.bisect_right(times, h + 60) - 1
        if i >= 0:
            out[h] = rows[i][1]
    return out


def r3(
    minutes: list, cycles: list, labelled: list, prices: dict, program: dict, settings
) -> RuleCounts:
    """Each minute's merged decision with every rule in act."""
    starts = [r.start for r in labelled]
    ends = {c.start: c.end for c in cycles}
    state = hr.RuleState()
    counts = RuleCounts(0, Counter(), Counter(), 0, 0)
    was = ""
    for m in minutes:
        if m.top is None or m.bottom is None:
            continue
        known = labelled[: bisect.bisect_right(starts, m.t)]
        open_ = [r for r in known[-2:] if r.start <= m.t <= ends[r.start]]
        boost = hb.status(known, m.t, hs.settings_at(m.t, hs.HISTORY_ANCHORS))
        last = known[-1].start if known else None
        current = open_[-1] if open_ else None
        ctx = hr.Context(
            m.t, m.top, m.bottom, 42.7, boost, current, last, prices, program, settings
        )
        decision = hr.decide(ctx, state, ALL_ACT)
        rule = decision.rule if decision.on else ""
        _count(counts, ctx, rule, was)
        was = rule
    return counts


def _count(counts: RuleCounts, ctx: hr.Context, rule: str, was: str) -> None:
    counts.minutes += 1
    if not rule:
        return
    counts.on[rule] += 1
    if rule != was:
        counts.starts[rule] += 1
    if ctx.boost.blocks_runs and rule not in ("boost_trigger", "flush"):
        counts.blocked_violations += 1
    price = hr.price_c(ctx, ctx.now)
    if rule == "comfort" and (price is None or price >= 15.0):
        counts.comfort_dear += 1


def _select(prices: pd.Series, n: float, limit: float, cap, budget: BudgetSettings, link) -> set:
    frame = pd.DataFrame({"heating_prio": prices * budget.pump_on_kw})
    frame = apply_budget(frame, limit, cap, budget)
    if link is not None:
        frame = slot_last(frame, link)
    return set(HeatingOptimizer().select_cheapest_hours(frame, n).index)


def day_boost(day_start: int, boosts: list) -> hb.BoostSettings:
    """A grid whose only expiry in the day is the recorded boost start."""
    inside = [b for b in boosts if day_start <= b < day_start + 86400]
    anchor = (inside[0] if inside else day_start - 7 * 86400) - 14 * 86400
    return hs.boost_settings(anchor, 14)


def r5_day(
    day: pd.DataFrame, n: float, limit: float, cap, budget: BudgetSettings, boosts: list
) -> dict:
    """The pump hours of a day with the switches off, budget on and link on."""
    start = int(day.index[0].timestamp())
    boost = day_boost(start, boosts)
    with_share = BudgetSettings(**{**budget.__dict__, "boost": BoostBudget(6.5, boost)})
    off = _select(day["price_total"], n, limit, cap, budget, None)
    shared = _select(day["price_total"], n, limit, cap, with_share, None)
    linked = _select(day["price_total"], n, limit, cap, budget, boost)
    slot = {
        t
        for t in day.index
        if hb.slots_between(int(t.timestamp()), int(t.timestamp()) + 3600, boost, boost.slot_s)
    }
    extra = [
        fixed_kw(t.to_pydatetime(), with_share) - fixed_kw(t.to_pydatetime(), budget)
        for t in day.index
    ]
    return {"off": off, "shared": shared, "linked": linked, "slot": slot, "extra_kw": extra}
