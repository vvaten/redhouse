"""Hot water relay rules (pure code): one decision function per rule and a
merge by priority, 3 > 2 > 4b > 4 > 7 > 1 (design 06 and 10).

Each rule returns a Want (ON with a reason) or None. A rule in dry_run is
decided and reported, but only rules in act switch the relay.
"""

import datetime
from dataclasses import dataclass, field
from typing import Callable, Optional

from src.common.local_time import HELSINKI
from src.control import hot_water_boost as hb

RULES = ("boost_trigger", "flush", "pre_on", "preheat", "comfort")
PRIORITY = ("flush", "boost_trigger", "pre_on", "preheat", "comfort")
MODES = ("off", "dry_run", "act")
# The bottom's idle fall (06: about 0.45 C/h, more in the evening)
IDLE_FALL_C_PER_H = 0.45
# A cycle this close before a rule's ON still counts as started by it
START_SLACK_S = 300
HOUR_S = 3600
QUARTER_S = 900
WEEKDAY_SLOTS = {4: "fri", 5: "sat"}
DONE = ":done"


@dataclass(frozen=True)
class Want:
    rule: str
    reason: str


@dataclass
class RuleState:
    """Per-minute memory of the rules, kept in RAM between ticks."""

    attempt_start: Optional[int] = None
    flush_for: Optional[int] = None
    flush_started: Optional[int] = None
    since: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Context:
    now: int
    top: Optional[float]
    bottom: Optional[float]
    start_level: float
    boost: hb.BoostStatus
    # The cycle running now, labelled, or None
    open_cycle: Optional[hb.Record]
    # The start of the newest cycle, open or closed
    last_cycle_start: Optional[int]
    # {hour start epoch: EUR/kWh}
    prices: dict
    # {quarter start epoch: ON, ALE or EVU} from the heating program
    program: dict
    settings: dict


def hour_of(t: int) -> int:
    return t - t % HOUR_S


def price_c(ctx: Context, t: int) -> Optional[float]:
    h = hour_of(t)
    return ctx.prices[h] * 100 if h in ctx.prices else None


def _run_started_since(ctx: Context, t: int) -> bool:
    return ctx.last_cycle_start is not None and ctx.last_cycle_start >= t - START_SLACK_S


def _held(state: RuleState, rule: str, ctx: Context, until: Callable[[int], int]) -> bool:
    """True while a rule's ON runs: before until(its start) and no run started."""
    if rule not in state.since:
        return False
    since = state.since[rule]
    if ctx.now >= until(since) or _run_started_since(ctx, since):
        del state.since[rule]
        # One ON per rule until its span ends, even when a run ended it early
        state.since[rule + DONE] = until(since)
        return False
    return True


def _start(state: RuleState, rule: str, now: int) -> bool:
    """Start the rule's ON unless its last one is still in its span."""
    done = rule + DONE
    if done in state.since:
        if now < state.since[done]:
            return False
        del state.since[done]
    if rule not in state.since:
        state.since[rule] = now
    return True


def _stop(state: RuleState, rule: str) -> None:
    if rule in state.since:
        del state.since[rule]


def boost_trigger(ctx: Context, state: RuleState) -> Optional[Want]:
    """Rule 2: in the slot, relay ON until a run starts or the attempt ends;
    retry after the gap. A boost that stalled near 51 C was a regular run."""
    b = ctx.boost
    s = ctx.settings["boost"]
    stalled = b.boost is not None and b.boost.stalled and ctx.open_cycle is None
    if not b.in_slot or not (b.pending or stalled):
        state.attempt_start = None
        return None
    if ctx.open_cycle is not None:
        return None
    attempt = state.attempt_start
    if attempt is not None and _run_started_since(ctx, attempt):
        # A run started: no retry, or after a stall not before the gap
        if not stalled or ctx.now < attempt + s.trigger_attempt_s + s.retry_gap_s:
            return None
    if attempt is None or ctx.now >= attempt + s.trigger_attempt_s + s.retry_gap_s:
        state.attempt_start = attempt = ctx.now
    if ctx.now < attempt + s.trigger_attempt_s:
        return Want("boost_trigger", "retry after a stalled climb" if stalled else "boost slot")
    return None


def flush(ctx: Context, state: RuleState) -> Optional[Want]:
    """Rule 3: in a boost, from a top of start_c, relay ON for a fixed time."""
    cfg = ctx.settings["flush"]
    cycle = ctx.open_cycle
    if cycle is not None and cycle.kind == hb.BOOST and state.flush_for != cycle.start:
        if ctx.top is not None and ctx.top >= float(cfg["start_c"]):
            state.flush_for, state.flush_started = cycle.start, ctx.now
    started = state.flush_started
    if started is not None and ctx.now < started + int(cfg["minutes"]) * 60:
        return Want("flush", "pipe flush in the boost")
    return None


def command_in_force(ctx: Context, t: int) -> Optional[str]:
    """The program's command at t: the last key at or before it."""
    keys = [k for k in ctx.program if k <= t]
    return ctx.program[max(keys)] if keys else None


def _dear(ctx: Context, h: int) -> bool:
    limit = float(ctx.settings["preheat"]["price_c_kwh"])
    p = price_c(ctx, h)
    in_evu = any(command_in_force(ctx, h + k * QUARTER_S) == "EVU" for k in range(4))
    return in_evu or (p is not None and p > limit)


def preheat_hour(ctx: Context) -> Optional[int]:
    """The cheapest hour before the dear window in which the projected bottom
    reaches the start level, or None when no window needs a pre-heat."""
    if ctx.bottom is None:
        return None
    cfg = ctx.settings["preheat"]
    hours_left = (ctx.bottom - ctx.start_level) / IDLE_FALL_C_PER_H
    if hours_left < 0 or hours_left > float(cfg["horizon_h"]):
        return None
    reach = hour_of(ctx.now + int(hours_left * HOUR_S))
    if not _dear(ctx, reach):
        return None
    window = reach
    while window - HOUR_S > hour_of(ctx.now) and _dear(ctx, window - HOUR_S):
        window -= HOUR_S
    before = {
        h: p for h in range(hour_of(ctx.now), window, HOUR_S) if (p := price_c(ctx, h)) is not None
    }
    if not before or _dear(ctx, hour_of(ctx.now)):
        return None
    return min(before, key=lambda h: before[h])


def _near_start(ctx: Context, reach_c: float) -> bool:
    return ctx.bottom is not None and ctx.bottom - ctx.start_level <= reach_c


def preheat(ctx: Context, state: RuleState) -> Optional[Want]:
    """Rule 4: a regular run in the cheapest hour before a dear window."""
    if ctx.boost.blocks_runs or ctx.open_cycle is not None:
        _stop(state, "preheat")
        return None
    if _held(state, "preheat", ctx, lambda since: hour_of(since) + HOUR_S):
        return Want("preheat", "pre-heat before a dear window")
    reach = float(ctx.settings["preheat"]["reach_c"])
    if preheat_hour(ctx) == hour_of(ctx.now) and _near_start(ctx, reach):
        if _start(state, "preheat", ctx.now):
            return Want("preheat", "pre-heat before a dear window")
    return None


def on_block_start(ctx: Context) -> Optional[int]:
    """The start of a program ON block at a quarter within the next hour,
    while the pump is not ON now, or None."""
    if command_in_force(ctx, ctx.now) == "ON":
        return None
    first = ctx.now - ctx.now % QUARTER_S + QUARTER_S
    for q in range(first, ctx.now + HOUR_S + 1, QUARTER_S):
        if command_in_force(ctx, q) == "ON":
            return q
    return None


def pre_on(ctx: Context, state: RuleState) -> Optional[Want]:
    """Rule 4b: a due regular run lead_min before a cheap ON block, so it
    ends before the block and its ALE to ON pulse."""
    cfg = ctx.settings["pre_on"]
    block = on_block_start(ctx)
    if block is None or ctx.boost.blocks_runs or ctx.open_cycle is not None:
        _stop(state, "pre_on")
        return None
    if _held(state, "pre_on", ctx, lambda since: block):
        return Want("pre_on", "regular run before an ON block")
    lead = int(cfg["lead_min"]) * 60
    # The run costs the price of the hour it starts in
    before, during = price_c(ctx, hour_of(block - lead)), price_c(ctx, hour_of(block))
    extra = float(cfg["max_extra_c_kwh"])
    cheap = before is not None and during is not None and before - during <= extra
    reach = float(ctx.settings["preheat"]["reach_c"])
    if block - lead <= ctx.now < block and cheap and _near_start(ctx, reach):
        if _start(state, "pre_on", ctx.now):
            return Want("pre_on", "regular run before an ON block")
    return None


def comfort_slot(ctx: Context) -> int:
    """Today's comfort slot start (epoch) for the local weekday."""
    local = datetime.datetime.fromtimestamp(ctx.now, HELSINKI)
    slots = ctx.settings["comfort"]["slots"]
    key = WEEKDAY_SLOTS[local.weekday()] if local.weekday() in WEEKDAY_SLOTS else "other"
    hh, mm = (int(x) for x in str(slots[key]).split(":"))
    start = HELSINKI.localize(datetime.datetime.combine(local.date(), datetime.time(hh, mm)))
    return int(start.timestamp())


def comfort(ctx: Context, state: RuleState) -> Optional[Want]:
    """Rule 7: circulation before the evening showers when the hour is cheap."""
    cfg = ctx.settings["comfort"]
    start = comfort_slot(ctx)
    if not start <= ctx.now < start + int(cfg["minutes"]) * 60:
        return None
    if ctx.boost.blocks_runs:
        return None
    p = price_c(ctx, ctx.now)
    if p is None or p >= float(cfg["price_c_kwh"]):
        return None
    low = ctx.bottom is None or ctx.bottom < ctx.start_level + 1.0
    if low and _dear(ctx, hour_of(ctx.now)):
        return None
    return Want("comfort", "comfort circulation before showers")


DECIDE = {
    "boost_trigger": boost_trigger,
    "flush": flush,
    "pre_on": pre_on,
    "preheat": preheat,
    "comfort": comfort,
}


@dataclass(frozen=True)
class Decision:
    on: bool
    rule: str
    reason: str
    # Rules in dry_run that want ON: {rule: reason}
    would: dict


def decide(ctx: Context, state: RuleState, modes: dict) -> Decision:
    """Every rule by its mode, merged by priority; rule 1 is the default OFF."""
    acting: dict = {}
    would: dict = {}
    for rule in PRIORITY:
        mode = modes[rule]
        if mode == "off":
            continue
        want = DECIDE[rule](ctx, state)
        if want is None:
            continue
        (acting if mode == "act" else would)[rule] = want.reason
    for rule in PRIORITY:
        if rule in acting:
            return Decision(True, rule, acting[rule], would)
    return Decision(False, "default_off", "no rule wants ON", would)
