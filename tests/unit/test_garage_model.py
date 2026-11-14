"""Tests for the garage thermal model, plan and tick decision."""

import pytest

from src.control.garage_model import (
    SLOT_S,
    GarageParams,
    Plan,
    Slot,
    decide,
    plan,
    plausible,
    simulate,
    step_c,
)

PARAMS = GarageParams(
    target_c=5.0,
    floor_c=2.0,
    stale_after_s=10800,
    loss_k_per_h_per_k=0.010,
    loss_k_per_h=0.08,
    heat_gross_k_per_h=0.6,
)
T0 = 1_800_000_000


def _slots(n, t_out, prices=None, start=T0):
    prices = prices if prices is not None else [0.10] * n
    return [Slot(start + i * SLOT_S, t_out, prices[i]) for i in range(n)]


def _cold_need(reading, t_out, n=96):
    """First slot that would end under the floor without heat."""
    temps = simulate(reading, _slots(n, t_out), [False] * n, PARAMS)
    return next(i for i, t in enumerate(temps) if t < PARAMS.floor_c)


class TestModel:
    def test_cooling_is_faster_with_a_larger_difference_to_outdoor(self):
        mild = 4.0 - step_c(4.0, 0.0, False, PARAMS)
        cold = 4.0 - step_c(4.0, -20.0, False, PARAMS)
        assert cold > mild > 0

    def test_cooling_never_goes_below_outdoor(self):
        assert step_c(0.01, 0.0, False, PARAMS) == pytest.approx(0.0)

    def test_heating_rises_less_in_colder_weather(self):
        mild = step_c(3.0, 0.0, True, PARAMS) - 3.0
        cold = step_c(3.0, -30.0, True, PARAMS) - 3.0
        assert mild > cold > 0

    def test_heating_stops_at_the_target(self):
        temps = simulate(4.99, _slots(4, 0.0), [True] * 4, PARAMS)
        assert max(temps) < 5.0 + 0.6 / 4
        assert temps[-1] < temps[0]


class TestPlan:
    def test_mild_weather_needs_no_heat(self):
        assert not any(plan(6.0, _slots(96, 5.0), PARAMS, 2.13).on)

    def test_a_cold_spell_keeps_the_garage_at_the_floor(self):
        p = plan(3.0, _slots(96, -20.0), PARAMS, 2.13)
        assert any(p.on)
        assert p.min_c >= PARAMS.floor_c - 1e-9

    def test_the_cheapest_slots_heat(self):
        prices = [0.30] * 96
        for cheap in (10, 11, 12):
            prices[cheap] = 0.01
        p = plan(2.5, _slots(96, -5.0, prices), PARAMS, 2.13)
        assert {10, 11, 12} <= {i for i, on in enumerate(p.on) if on}

    def test_no_slot_heats_above_the_target(self):
        p = plan(4.9, _slots(96, -20.0), PARAMS, 2.13)
        assert max(p.modelled_c) <= PARAMS.target_c + 0.6 / 4

    def test_unknown_prices_heat_only_when_needed_and_late(self):
        """Before 14:00 the prices end at midnight: no preheat into an unknown price."""
        prices = [0.10] * 8 + [None] * 88
        p = plan(4.0, _slots(96, -15.0, prices), PARAMS, 2.13)
        unpriced_on = [i for i in range(8, 96) if p.on[i]]
        assert unpriced_on
        assert min(unpriced_on) >= _cold_need(4.0, -15.0) - 8
        assert p.min_c >= PARAMS.floor_c - 1e-9

    def test_a_need_past_the_known_prices_does_not_buy_todays_peak(self):
        """Mild while the prices are known, a cold night after: the priced slots
        heat only up to the end margin, and the night heats in its own slots."""
        slots = [Slot(T0 + i * SLOT_S, 0.0, 0.30) for i in range(44)]
        slots += [Slot(T0 + i * SLOT_S, -25.0, None) for i in range(44, 96)]
        p = plan(3.5, slots, PARAMS, 2.13)
        assert sum(p.on[:44]) <= 8
        assert p.modelled_c[43] < PARAMS.floor_c + PARAMS.end_margin_k + 0.6
        assert any(p.on[44:])
        assert p.min_c >= PARAMS.floor_c - 1e-9

    def test_the_end_of_the_priced_slots_keeps_a_margin(self):
        prices = [0.10] * 8 + [None] * 88
        p = plan(3.1, _slots(96, -10.0, prices), PARAMS, 2.13)
        assert p.modelled_c[7] >= PARAMS.floor_c + PARAMS.end_margin_k - 1e-9

    def test_without_unpriced_slots_there_is_no_end_margin(self):
        p = plan(3.2, _slots(8, 0.0), PARAMS, 2.13)
        assert not any(p.on)

    def test_a_reading_under_the_floor_still_plans_the_rest(self):
        """The first shortfall cannot be fixed; later ones must still get heat."""
        p = plan(1.0, _slots(96, -20.0), PARAMS, 2.13)
        assert sum(p.on) > 40
        assert min(p.modelled_c[48:]) >= PARAMS.floor_c - 1e-9

    def test_a_slot_without_headroom_is_not_used(self):
        slots = _slots(8, -20.0)
        slots[0] = Slot(slots[0].start, -20.0, 0.01, headroom_kw=1.0)
        assert not plan(2.2, slots, PARAMS, 2.13).on[0]

    def test_the_plan_records_its_start(self):
        assert plan(3.0, _slots(4, 0.0), PARAMS, 2.13).start == T0


class TestDecide:
    def _plan(self, first_on, start=T0):
        return Plan(start, (first_on, False, False, False), (3.0,) * 4, 3.0)

    def test_a_stale_reading_heats(self):
        d = decide(T0, 4.0, T0 - 10801, self._plan(False), False, PARAMS)
        assert d.on and d.reason == "stale_reading"

    def test_no_reading_heats(self):
        assert decide(T0, None, None, self._plan(False), False, PARAMS).on

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), 99.0, -60.0])
    def test_an_implausible_reading_heats(self, bad):
        d = decide(T0, bad, T0, self._plan(False), False, PARAMS)
        assert d.on and d.reason == "stale_reading"
        assert not plausible(bad)

    def test_under_the_floor_heats_even_with_a_guard_override(self):
        d = decide(T0, 1.9, T0, self._plan(False), True, PARAMS)
        assert d.on and d.reason == "under_floor"

    def test_at_the_target_stops(self):
        d = decide(T0, 5.0, T0, self._plan(True), False, PARAMS)
        assert not d.on and d.reason == "at_target"

    def test_the_guard_override_wins_above_the_floor(self):
        d = decide(T0, 3.0, T0, self._plan(True), True, PARAMS)
        assert not d.on and d.reason == "overridden_by_guard"

    @pytest.mark.parametrize("offset", [0, 20, 300, SLOT_S - 1])
    def test_the_plan_decides_anywhere_in_its_slot(self, offset):
        assert decide(T0 + offset, 3.0, T0, self._plan(True), False, PARAMS).on
        assert not decide(T0 + offset, 3.0, T0, self._plan(False), False, PARAMS).on

    def test_a_plan_for_another_slot_is_not_used(self):
        d = decide(T0 + SLOT_S, 3.0, T0 + SLOT_S, self._plan(True), False, PARAMS)
        assert not d.on and d.reason == "no_plan"
