"""Tests for the 1-minute load guard core."""

import pytest

from src.control import load_guard as lg

HOUR = 1_800_000_000 - 1_800_000_000 % 3600


def _samples(minutes, kw_per_phase, start=HOUR, wh0=0.0, import0=None):
    """One sample a minute at a constant per-phase load. A negative total is
    export: the net counter falls, the import counter stays."""
    out = []
    wh = wh0
    imp = wh0 if import0 is None else import0
    for m in range(minutes):
        out.append(lg.Sample(start + m * 60, kw_per_phase, wh, imp))
        step = sum(kw_per_phase) * 1000 / 60
        wh += step
        imp += max(step, 0.0)
    return out


SETTINGS = lg.GuardSettings(garage_kw=2.13, pump_ale_kw=3.0)


def _series(totals_kw):
    """One sample a minute from the hour's start, with the given total kW each minute."""
    out, wh = [], 0.0
    for m, kw in enumerate(totals_kw):
        out.append(lg.Sample(HOUR + m * 60, (kw / 3,) * 3, wh, wh))
        wh += kw * 1000 / 60
    return out


# 9 kW until a shed at :20; the shed shows from :21:30
SHED_AT = HOUR + 20 * 60
AFTER = SHED_AT + 90
GARAGE = [lg.Shed("garage_off", 2.13)]


class TestAfterAShed:
    def test_a_shed_that_works_ends_the_excess(self):
        samples = _series([9.0] * 21 + [3.0] * 3)
        now = HOUR + 23 * 60
        plain = lg.projected_hour_kw(samples, now)
        after = lg.projected_hour_kw(samples, now, AFTER)
        limit = (plain + after) / 2
        assert lg.judge(samples, now, 1.0, limit, GARAGE).sheds == tuple(GARAGE)
        verdict = lg.judge(samples, now, 1.0, limit, GARAGE, AFTER)
        assert not verdict.over and verdict.sheds == ()
        assert verdict.projected_kw == pytest.approx(after)

    @pytest.mark.parametrize("at_22, sheds", [(3.0, ()), (9.0, tuple(GARAGE))])
    def test_one_high_reading_after_a_shed_confirms_nothing(self, at_22, sheds):
        """A compressor start at :23 alone sheds nothing; high at :22 and :23 does."""
        samples = _series([9.0] * 21 + [3.0, at_22, 9.0])
        verdict = lg.judge(samples, HOUR + 23 * 60, 1.0, 6.0, GARAGE, AFTER)
        assert verdict.sheds == sheds


class TestProjection:
    def test_a_steady_load_projects_itself(self):
        samples = _samples(31, (1.0, 1.0, 1.0))
        assert lg.projected_hour_kw(samples, HOUR + 30 * 60) == pytest.approx(3.0, abs=0.05)

    def test_a_new_load_counts_only_for_the_rest_of_the_hour(self):
        early = _samples(30, (1.0, 1.0, 1.0))
        late = _samples(5, (3.0, 3.0, 3.0), start=HOUR + 30 * 60, wh0=early[-1].net_wh + 50)
        projected = lg.projected_hour_kw(early + late, HOUR + 34 * 60)
        assert 5.5 < projected < 6.5

    def test_after_a_shed_only_the_later_power_counts(self):
        """3 kW a phase until :20, then 1 kW: the 5-minute mean still holds the 3 kW."""
        high = _samples(21, (3.0, 3.0, 3.0))
        low = _samples(3, (1.0, 1.0, 1.0), start=HOUR + 21 * 60, wh0=high[-1].net_wh + 150)
        now = HOUR + 23 * 60
        mixed = lg.projected_hour_kw(high + low, now)
        after = lg.projected_hour_kw(high + low, now, power_after=HOUR + 20 * 60 + 30)
        rest_h = (3600 - 23 * 60) / 3600
        assert after == pytest.approx(mixed - (9.0 - 3.0) * 2 / 5 * rest_h, abs=0.01)
        assert after < mixed

    def test_no_sample_in_the_hour_gives_none(self):
        samples = _samples(10, (1.0, 1.0, 1.0), start=HOUR - 3600)
        assert lg.projected_hour_kw(samples, HOUR + 60) is None

    def test_a_meter_reset_gives_none(self):
        samples = _samples(10, (1.0, 1.0, 1.0), wh0=100000.0)
        samples.append(lg.Sample(HOUR + 600, (1.0, 1.0, 1.0), 5.0, 5.0))
        assert lg.projected_hour_kw(samples, HOUR + 600) is None

    def test_battery_export_then_a_car_still_projects(self):
        """Export makes the net counter fall; that is not a meter reset."""
        export = _samples(20, (-1.7, -1.7, -1.7), wh0=50000.0)
        last = export[-1]
        car = _samples(
            9, (3.7, 3.7, 3.7), start=HOUR + 20 * 60, wh0=last.net_wh, import0=last.import_wh
        )
        projected = lg.projected_hour_kw(export + car, HOUR + 28 * 60)
        assert projected is not None
        assert 3.0 < projected < 6.0

    def test_a_counter_glitch_gives_none(self):
        samples = _samples(10, (1.0, 1.0, 1.0))
        samples.append(lg.Sample(HOUR + 600, (1.0, 1.0, 1.0), 20000.0, 20000.0))
        assert lg.projected_hour_kw(samples, HOUR + 600) is None

    def test_the_start_of_the_hour_is_interpolated_after_a_gap(self):
        before = lg.Sample(HOUR - 300, (1.0, 1.0, 1.0), 0.0, 0.0)
        # 3 kW from 5 min before the hour to 20 min in, the first sample at :20
        first = lg.Sample(HOUR + 1200, (3.0, 3.0, 3.0), 1250.0, 1250.0)
        projected = lg.projected_hour_kw([before, first], HOUR + 1200)
        assert projected == pytest.approx(1.0 + 9.0 * 40 / 60, abs=0.05)

    def test_the_minutes_before_the_first_sample_run_at_its_power(self):
        samples = _samples(10, (1.0, 1.0, 1.0), start=HOUR + 20 * 60)
        assert lg.projected_hour_kw(samples, HOUR + 29 * 60) == pytest.approx(3.0, abs=0.05)


class TestSteps:
    def _with_step(self, before, after):
        return _samples(5, before, start=HOUR) + _samples(3, after, start=HOUR + 5 * 60, wh0=1000.0)

    @pytest.mark.parametrize(
        "after, label",
        [
            ((1.9, 1.9, 1.9), "car_5kw"),
            ((3.7, 3.7, 3.7), "car_11kw"),
            ((2.6, 2.6, 2.6), "sauna"),
            ((2.15, 2.15, 2.15), "resistor_6kw"),
            ((1.07, 0.0, 0.0), "garage_heater"),
            ((0.0, 0.0, 1.07), "garage_heater"),
            ((0.0, 1.05, 0.0), "single_phase_l2"),
            ((1.0, 1.0, 1.0), "compressor"),
            ((1.07, 0.0, 1.06), "garage_heater"),
        ],
    )
    def test_a_step_is_named_by_its_size_per_phase(self, after, label):
        samples = self._with_step((0.0, 0.0, 0.0), after)
        step = lg.phase_step(samples, samples[-1].t)
        assert lg.classify_step(step) == label

    def test_no_step_gives_no_label(self):
        samples = self._with_step((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
        assert lg.classify_step(lg.phase_step(samples, samples[-1].t)) is None

    def test_too_few_samples_give_no_step(self):
        assert lg.phase_step(_samples(3, (1.0, 1.0, 1.0)), HOUR + 120) is None


class TestSheds:
    def test_under_the_limit_nothing_is_shed(self):
        samples = _samples(31, (2.0, 2.0, 2.0))
        verdict = lg.judge(
            samples, HOUR + 30 * 60, 1.0, 7.9, lg.available_sheds(SETTINGS, True, "ON")
        )
        assert verdict.sheds == ()

    def test_the_garage_goes_first_and_the_pump_only_if_needed(self):
        samples = _samples(31, (2.9, 2.9, 2.9))
        available = lg.available_sheds(SETTINGS, True, "ON")
        verdict = lg.judge(samples, HOUR + 30 * 60, 1.0, 7.9, available)
        assert [s.load for s in verdict.sheds] == ["garage_off"]
        bigger = lg.judge(_samples(31, (3.3, 3.3, 3.3)), HOUR + 30 * 60, 1.0, 7.9, available)
        assert [s.load for s in bigger.sheds][:2] == ["garage_off", "pump_ale"]

    def test_a_shed_saves_only_the_rest_of_the_hour(self):
        both = [lg.Shed("garage_off", 2.13), lg.Shed("pump_ale", 3.0)]
        assert lg.sheds_needed(9.0, 1.0, 7.9, HOUR, both) == (both[0],)
        assert lg.sheds_needed(9.0, 1.0, 7.9, HOUR + 59 * 60, both) == tuple(both)

    def test_a_short_spike_sheds_nothing_and_a_held_excess_sheds(self):
        available = lg.available_sheds(SETTINGS, True, "ON")
        base = _samples(30, (1.0, 1.0, 1.0))
        spike = _samples(3, (6.0, 6.0, 6.0), start=HOUR + 30 * 60, wh0=base[-1].net_wh)
        verdict = lg.judge(base + spike, HOUR + 32 * 60, 1.0, 7.9, available)
        assert not verdict.over and verdict.sheds == ()
        held = _samples(8, (6.0, 6.0, 6.0), start=HOUR + 30 * 60, wh0=base[-1].net_wh)
        verdict = lg.judge(base + held, HOUR + 37 * 60, 1.0, 7.9, available)
        assert verdict.over and verdict.sheds

    def test_over_with_nothing_to_shed_is_reported(self):
        samples = _samples(31, (3.3, 3.3, 3.3))
        verdict = lg.judge(samples, HOUR + 30 * 60, 1.0, 7.9, [])
        assert verdict.over and verdict.sheds == ()

    def test_night_hours_count_at_their_factor(self):
        samples = _samples(31, (2.9, 2.9, 2.9))
        verdict = lg.judge(
            samples, HOUR + 30 * 60, 0.8, 7.9, lg.available_sheds(SETTINGS, True, "ON")
        )
        assert verdict.sheds == ()
        assert verdict.counted_kw == pytest.approx(verdict.projected_kw * 0.8)

    @pytest.mark.parametrize(
        "garage, pump, loads",
        [
            (True, "ON", ["garage_off", "pump_ale"]),
            (False, "ON", ["pump_ale"]),
            # ALE draws nothing to shed, and EVU is never a shed
            (False, "ALE", []),
            (True, "ALE", ["garage_off"]),
            (False, "EVU", []),
            (False, None, []),
        ],
    )
    def test_only_loads_that_run_can_be_shed_and_never_to_evu(self, garage, pump, loads):
        assert [s.load for s in lg.available_sheds(SETTINGS, garage, pump)] == loads
