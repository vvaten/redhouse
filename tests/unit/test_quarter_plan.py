"""The quarter selection of design 12: cheapest N, caps per hour, the gap."""

import itertools
import random
import time

import pytest

from src.control import quarter_plan as qp


def _hours(q: int) -> list:
    return [i // 4 for i in range(q)]


def _caps(q: int, k: int = 4) -> dict:
    return {h: k for h in set(_hours(q))}


def _runs(on: list, lead: int) -> tuple:
    """(OFF runs before each ON, the tail run after the last ON)."""
    runs, run = [], lead
    for x in on:
        if x:
            runs.append(run)
            run = 0
        else:
            run += 1
    return runs, run


def _brute(costs, hours, caps, n, max_off, lead, end_counts):
    best = None
    for picks in itertools.combinations(range(len(costs)), n):
        on = [i in picks for i in range(len(costs))]
        # A gap already past the limit forces ON at the first quarter
        runs, tail = _runs(on, lead if max_off is None else min(lead, max_off))
        if max_off is not None:
            if any(r > max_off for r in runs) or (end_counts and tail > max_off):
                continue
        over = qp._over(on, hours, caps)
        key = (over, sum(c for c, x in zip(costs, on) if x))
        if best is None or key < best:
            best = key
    return best


def test_no_limits_pick_the_cheapest():
    costs = [5, 1, 4, 2, 8, 3, 7, 6]
    result = qp.plan(costs, _hours(8), _caps(8), 3)
    assert [i for i, x in enumerate(result.on) if x] == [1, 3, 5]
    assert result.cost == 6 and result.over_cap == 0


def test_the_hour_cap_spreads_the_quarters():
    """Hour 0 is cheapest but takes only 2 quarters."""
    costs = [1, 1, 1, 1, 5, 6, 7, 8]
    caps = {0: 2, 1: 4}
    result = qp.plan(costs, _hours(8), caps, 3)
    assert sum(result.on[:4]) == 2 and result.on[4]
    assert result.over_cap == 0


def test_over_cap_quarters_are_used_only_when_the_day_needs_them():
    costs = [1] * 8
    result = qp.plan(costs, _hours(8), {0: 2, 1: 2}, 6)
    assert sum(result.on) == 6 and result.over_cap == 2


def test_the_gap_limit_moves_heating_into_a_long_dear_stretch():
    """Cheap at both ends of the day; 4 h at most without heating."""
    costs = [1.0] * 8 + [9.0] * 80 + [1.0] * 8
    hours = _hours(96)
    free = qp.plan(costs, hours, _caps(96), 8)
    comfy = qp.plan(costs, hours, _caps(96), 8, max_off=16)
    assert qp.longest_off(list(free.on)) > 16
    assert qp.longest_off(list(comfy.on)) <= 16 and comfy.gap_kept
    assert comfy.cost > free.cost


def test_yesterdays_tail_opens_the_first_gap():
    """Yesterday's last ON was 3 h ago: today must heat within 1 h."""
    costs = [9.0] * 8 + [1.0] * 40
    result = qp.plan(costs, _hours(48), _caps(48), 4, max_off=16, lead_off=12, end_counts=False)
    first = result.on.index(True)
    assert first <= 4


def test_the_tail_counts_as_a_gap_unless_told_not_to():
    costs = [1.0] * 8 + [9.0] * 40
    counted = qp.plan(costs, _hours(48), _caps(48), 4, max_off=16)
    free = qp.plan(costs, _hours(48), _caps(48), 4, max_off=16, end_counts=False)
    assert len(counted.on) - 1 - max(i for i, x in enumerate(counted.on) if x) <= 16
    assert free.cost <= counted.cost


def test_an_impossible_gap_is_dropped():
    result = qp.plan([1.0] * 96, _hours(96), _caps(96), 1, max_off=8)
    assert not result.gap_kept and sum(result.on) == 1


def test_no_gap_limit_is_not_kept():
    assert not qp.plan([1.0] * 8, _hours(8), _caps(8), 2).gap_kept


@pytest.mark.parametrize("seed", range(40))
def test_matches_a_brute_force_search(seed):
    rng = random.Random(seed)
    q = rng.choice([8, 10, 12])
    costs = [round(rng.uniform(0, 10), 2) for _ in range(q)]
    hours = _hours(q)
    caps = {h: rng.choice([1, 2, 3, 4]) for h in set(hours)}
    n = rng.randint(1, 5)
    max_off = rng.choice([None, 2, 3, 4])
    lead = rng.randint(0, 3)
    end_counts = rng.choice([True, False])
    result = qp.plan(costs, hours, caps, n, max_off, lead, end_counts)
    expected = _brute(costs, hours, caps, n, max_off, lead, end_counts)
    if expected is None:
        assert not result.gap_kept
        return
    assert result.gap_kept or max_off is None
    assert (result.over_cap, pytest.approx(result.cost)) == expected


def test_bad_input_is_refused():
    with pytest.raises(ValueError):
        qp.plan([1.0], [0, 1], {0: 4, 1: 4}, 1)
    with pytest.raises(ValueError):
        qp.plan([1.0], [0], {0: 4}, 2)
    with pytest.raises(ValueError):
        qp.plan([1.0], [0], {}, 1)


def test_a_full_day_is_fast_enough():
    rng = random.Random(1)
    costs = [rng.uniform(0, 0.3) for _ in range(100)]
    started = time.perf_counter()
    qp.plan(costs, _hours(100), _caps(100, 3), 64, max_off=48, lead_off=10)
    assert time.perf_counter() - started < 5.0
