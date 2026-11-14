"""Garage thermal model, 24 h heating plan and the decision of one tick.

Pure functions: time, readings, prices and forecasts come in as
arguments, so the live controller and the simulator share this code.
"""

import math
from dataclasses import dataclass
from typing import Optional

SLOT_S = 900
# A reading outside this range is a sensor fault, treated as no reading
PLAUSIBLE_C = (-40.0, 60.0)
# A reading newer than now by more than this means the clock stepped back
FUTURE_TOLERANCE_S = 60


@dataclass(frozen=True)
class GarageParams:
    target_c: float
    floor_c: float
    stale_after_s: float
    loss_k_per_h_per_k: float
    loss_k_per_h: float
    # Heater rise before losses: heater power over the heat capacity
    heat_gross_k_per_h: float
    # Before unpriced slots, the last priced slot keeps this much over the floor
    end_margin_k: float = 1.0


@dataclass(frozen=True)
class Slot:
    start: int
    t_out_c: float
    price: Optional[float]
    # None: no power cap known for the slot
    headroom_kw: Optional[float] = None


@dataclass(frozen=True)
class Plan:
    start: int
    on: tuple[bool, ...]
    modelled_c: tuple[float, ...]
    min_c: float


@dataclass(frozen=True)
class Decision:
    on: bool
    reason: str


def step_c(t_c: float, t_out_c: float, on: bool, params: GarageParams) -> float:
    """Temperature after one slot. Without heat it cools at most to outdoor."""
    hours = SLOT_S / 3600
    loss = (params.loss_k_per_h_per_k * max(t_c - t_out_c, 0.0) + params.loss_k_per_h) * hours
    if on:
        return t_c + params.heat_gross_k_per_h * hours - loss
    return max(t_c - loss, min(t_c, t_out_c))


def simulate(reading_c: float, slots: list, on: list, params: GarageParams) -> list:
    """Modelled temperature at the end of each slot."""
    temps = []
    t = reading_c
    for slot, heat in zip(slots, on):
        t = step_c(t, slot.t_out_c, heat and t < params.target_c, params)
        temps.append(t)
    return temps


def _last_priced(slots: list) -> Optional[int]:
    priced = [k for k, s in enumerate(slots) if s.price is not None]
    return priced[-1] if priced else None


def _shortfalls(temps: list, slots: list, params: GarageParams, skip: set) -> Optional[int]:
    """First slot not in skip that ends under the floor, or the last priced
    slot under the end margin when unpriced slots follow it."""
    for k, t in enumerate(temps):
        if t < params.floor_c and k not in skip:
            return k
    last = _last_priced(slots)
    if last is not None and last < len(slots) - 1 and last not in skip:
        if temps[last] < params.floor_c + params.end_margin_k:
            return last
    return None


def _candidate(
    k: int, slots: list, on: list, temps_before: list, params: GarageParams, heater_kw: float
) -> Optional[int]:
    """The slot to heat for a shortfall at k: the cheapest priced off slot up
    to k, or, past the known prices, the latest unpriced one (heat late)."""
    last = _last_priced(slots)
    lowest = last + 1 if last is not None and k > last else 0
    free = [
        j
        for j in range(lowest, k + 1)
        if not on[j]
        and temps_before[j] < params.target_c
        and (slots[j].headroom_kw is None or slots[j].headroom_kw >= heater_kw)
    ]
    priced = [j for j in free if slots[j].price is not None]
    if priced:
        return min(priced, key=lambda j: (slots[j].price, j))
    return max(free) if free else None


def plan(reading_c: float, slots: list, params: GarageParams, heater_kw: float) -> Plan:
    """Fewest and cheapest heating slots that keep the modelled garage at the
    floor. A shortfall no slot can fix is passed over, so later ones still plan."""
    on = [False] * len(slots)
    skip: set = set()
    while True:
        temps = simulate(reading_c, slots, on, params)
        k = _shortfalls(temps, slots, params, skip)
        if k is None:
            break
        temps_before = [reading_c] + temps[:-1]
        j = _candidate(k, slots, on, temps_before, params, heater_kw)
        if j is None:
            skip.add(k)
            continue
        on[j] = True
    temps = simulate(reading_c, slots, on, params)
    start = slots[0].start if slots else 0
    return Plan(start, tuple(on), tuple(temps), min([reading_c] + temps))


def plausible(reading_c: Optional[float]) -> bool:
    if reading_c is None or not math.isfinite(reading_c):
        return False
    return PLAUSIBLE_C[0] <= reading_c <= PLAUSIBLE_C[1]


def decide(
    now: int,
    reading_c: Optional[float],
    reading_time: Optional[float],
    garage_plan: Optional[Plan],
    override_off: bool,
    params: GarageParams,
) -> Decision:
    """The rule for this tick: safety first, then the guard, then the plan."""
    if (
        reading_c is None
        or not plausible(reading_c)
        or reading_time is None
        or now - reading_time > params.stale_after_s
        or now - reading_time < -FUTURE_TOLERANCE_S
    ):
        return Decision(True, "stale_reading")
    if reading_c < params.floor_c:
        return Decision(True, "under_floor")
    if reading_c >= params.target_c:
        return Decision(False, "at_target")
    if override_off:
        return Decision(False, "overridden_by_guard")
    if garage_plan is None or not garage_plan.on:
        return Decision(False, "no_plan")
    if not garage_plan.start <= now < garage_plan.start + SLOT_S:
        return Decision(False, "no_plan")
    return Decision(garage_plan.on[0], "plan_heat" if garage_plan.on[0] else "plan_coast")
