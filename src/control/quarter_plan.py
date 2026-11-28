"""Pick the pump's ON quarters: the cheapest N, with a cap per hour and a
longest gap without heating (design 12).

Pure functions. An exact dynamic program over the quarters: the state is
(quarters chosen, OFF quarters since the last ON, ON quarters in the
current hour). Quarters over an hour's cap are allowed but cost
OVER_CAP_PENALTY each, so the plan uses them only when the day needs them.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np

QUARTERS_PER_HOUR = 4
OVER_CAP_PENALTY = 1000.0


@dataclass(frozen=True)
class QuarterPlan:
    on: tuple
    cost: float
    over_cap: int
    gap_kept: bool


def _check(costs: list, hours: list, caps: dict, n: int) -> None:
    if len(costs) != len(hours):
        raise ValueError("costs and hours differ in length")
    if not 0 <= n <= len(costs):
        raise ValueError(f"Cannot pick {n} of {len(costs)} quarters")
    missing = sorted({h for h in hours if h not in caps})
    if missing:
        raise ValueError(f"No cap for hours {missing}")


def _solve(costs: list, hours: list, caps: dict, n: int, gap: int, lead: tuple, tail_free: bool):
    """The best plan as a list of bools, or None when no plan keeps the gap.
    Gap index gap + 1 is a tail past the limit: no ON may follow it."""
    over = gap + 1
    shape = (n + 1, gap + 2, QUARTERS_PER_HOUR + 1)
    dp = np.full(shape, np.inf)
    lead_off, lead_on = lead
    dp[0, min(lead_off, gap), lead_on] = 0.0
    steps = []
    for i in range(len(costs)):
        reset_from = None
        if i > 0 and hours[i] != hours[i - 1]:
            reset_from = dp.argmin(axis=2)
            collapsed = np.full(shape, np.inf)
            collapsed[:, :, 0] = dp.min(axis=2)
            dp = collapsed
        new = np.full(shape, np.inf)
        # OFF: the gap grows by one; at the limit it enters the tail state
        new[:, 1:over, :] = dp[:, : over - 1, :]
        stay = dp[:, over, :] < dp[:, gap, :]
        new[:, over, :] = np.where(stay, dp[:, over, :], dp[:, gap, :])
        # ON: the gap closes; past the hour's cap each quarter costs the penalty
        within = dp[:, :over, :]
        on_from = within.argmin(axis=1)
        best = within.min(axis=1)
        price = np.array(
            [costs[i] + (OVER_CAP_PENALTY if c >= caps[hours[i]] else 0.0) for c in range(4)]
        )
        on_cost = best[:-1, :-1] + price
        new[1:, 0, 1:] = on_cost
        steps.append((reset_from, on_from, stay))
        dp = new
    final = dp[n, : (over + 1 if tail_free else over), :]
    if not np.isfinite(final).any():
        return None
    g, c = np.unravel_index(np.argmin(final), final.shape)
    return _back(steps, n, int(g), int(c), over)


def _back(steps: list, n: int, g: int, c: int, over: int) -> list:
    on = [False] * len(steps)
    for i in range(len(steps) - 1, -1, -1):
        reset_from, on_from, stay = steps[i]
        if g == 0:
            on[i] = True
            g = int(on_from[n - 1, c - 1])
            n, c = n - 1, c - 1
        elif g == over:
            g = over if stay[n, c] else over - 1
        else:
            g -= 1
        if reset_from is not None:
            c = int(reset_from[n, g])
    return on


def plan(
    costs: list,
    hours: list,
    caps: dict,
    n: int,
    max_off: Optional[int] = None,
    lead_off: int = 0,
    end_counts: bool = True,
    lead_on: int = 0,
) -> QuarterPlan:
    """The cheapest n ON quarters. costs: EUR of one ON quarter each; hours:
    the hour key of each quarter; caps: {hour key: ON quarters that fit}.
    max_off: the most OFF quarters in a row (None: no limit); lead_off: OFF
    quarters before the first one; end_counts: the tail is a gap too;
    lead_on: ON quarters the first quarter's hour already has before it.
    Without a plan that keeps max_off, the gap is dropped."""
    _check(costs, hours, caps, n)
    if not 0 <= lead_on < QUARTERS_PER_HOUR:
        raise ValueError(f"lead_on must be 0 to 3, not {lead_on}")
    loose = len(costs) + lead_off
    gap = loose if max_off is None else max_off
    on = _solve(costs, hours, caps, n, gap, (lead_off, lead_on), not end_counts)
    kept = on is not None and max_off is not None
    if on is None:
        on = _solve(costs, hours, caps, n, loose, (lead_off, lead_on), True)
    if on is None:
        raise RuntimeError("No plan without a gap limit: a bug")
    over = _over(on, hours, caps, {hours[0]: lead_on} if hours else {})
    return QuarterPlan(tuple(on), _cost(on, costs), over, kept)


def _cost(on: list, costs: list) -> float:
    return float(sum(c for c, x in zip(costs, on) if x))


def _over(on: list, hours: list, caps: dict, already: Optional[dict] = None) -> int:
    used: dict = dict(already) if already else {}
    for h, x in zip(hours, on):
        if x:
            used[h] = (used[h] if h in used else 0) + 1
    return sum(max(0, k - caps[h]) for h, k in used.items())


def longest_off(on: list, lead_off: int = 0) -> int:
    """The longest run of OFF quarters, the lead counted into the first."""
    best = run = lead_off
    for x in on:
        run = 0 if x else run + 1
        best = max(best, run)
    return best
