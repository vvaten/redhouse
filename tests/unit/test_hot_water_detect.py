"""The cycle detector on fixed minute frames cut from the house's own data
(tests/unit/fixtures/hot_water, local times in the names)."""

import csv
import datetime
from pathlib import Path

import pytest

from src.common.local_time import HELSINKI
from src.control import hot_water_detect as hd

FIXTURES = Path(__file__).parent / "fixtures" / "hot_water"


def _num(row: dict, key: str):
    return float(row[key]) if row[key] else None


def frame(name: str) -> list:
    """The fixture's minutes: t (UTC epoch), tank top and bottom (C), phases (kW)."""
    rows = []
    with open(FIXTURES / f"{name}.csv", newline="") as f:
        for r in csv.DictReader(f):
            p = (_num(r, "p1"), _num(r, "p2"), _num(r, "p3"))
            phases = None if any(v is None for v in p) else p
            rows.append(hd.Minute(int(r["t"]), _num(r, "top"), _num(r, "bottom"), phases))
    return rows


def local(text: str) -> int:
    naive = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")
    return int(HELSINKI.localize(naive).timestamp())


def cycles(name: str) -> list:
    minutes = frame(name)
    return hd.find_cycles(minutes, minutes[-1].t + 3600)


class TestRuns:
    def test_a_regular_run(self):
        found = cycles("regular")
        assert len(found) == 1
        c = found[0]
        assert abs(c.start - local("2026-11-16 13:34")) <= 5 * 60
        assert c.peak_top < 52 and not c.heater and not c.open
        assert 0.5 < c.kwh < 2.5

    def test_a_draw_started_run(self):
        found = cycles("draw_started")
        assert len(found) == 1
        assert abs(found[0].start - local("2026-11-16 21:28")) <= 5 * 60
        assert found[0].peak_top < 55

    def test_a_boost_and_the_heater_run_on_a_draw_after_it(self):
        found = cycles("boost")
        assert len(found) == 2
        assert found[1].heater and found[1].start_top > 55
        c = found[0]
        assert abs(c.start - local("2026-11-22 09:28")) <= 5 * 60
        assert c.peak_top > 68
        assert c.kwh == pytest.approx(11.7, rel=0.2)

    def test_the_alarm_boost_with_the_heater_alone_from_22_38(self):
        found = [c for c in cycles("alarm_boost") if c.start < local("2026-11-16 00:10")]
        assert len(found) == 1
        assert abs(found[0].start - local("2026-11-15 22:26")) <= 5 * 60
        assert found[0].peak_top > 62


class TestNotHotWater:
    def test_the_nov_17_house_stages_make_no_cycle(self):
        for t in ("2026-11-17 00:02", "2026-11-17 05:02"):
            near = [c for c in cycles("stages_nov17") if abs(c.start - local(t)) < 30 * 60]
            assert near == [], t

    def test_the_car_on_nov_20_makes_no_cycle(self):
        found = cycles("car_nov20")
        assert [
            c for c in found if local("2026-11-20 09:00") <= c.start <= local("2026-11-20 09:30")
        ] == []

    def test_a_battery_swing_makes_no_cycle(self):
        quiet = [m for m in frame("regular") if m.t < local("2026-11-16 13:30")]
        swung = [
            hd.Minute(
                m.t, m.top, m.bottom, tuple(p + (2.0 if i % 10 < 5 else -2.0) for p in m.phases)
            )
            for i, m in enumerate(quiet)
        ]
        assert hd.find_cycles(swung, swung[-1].t + 3600) == []

    def test_the_sauna_start_on_a_hot_tank_is_no_cycle(self):
        found = cycles("sauna_nov14")
        assert [c for c in found if c.start < local("2026-11-14 19:00")] == []


class TestHeaterRuns:
    def test_the_sauna_evening_heater_pulses_are_heater_cycles(self):
        found = [c for c in cycles("sauna_nov14") if c.start >= local("2026-11-14 19:00")]
        assert len(found) >= 3
        assert all(c.heater and c.start_top > 55 for c in found)


class TestSeries:
    def test_short_gaps_are_filled_per_field(self):
        minutes = [
            hd.Minute(0, 40.0, 40.0, (1.0, 1.0, 1.0)),
            hd.Minute(60, None, None, (1.0, 1.0, 1.0)),
            hd.Minute(180, 44.0, 43.0, (3.0, 3.0, 3.0)),
        ]
        s = hd.Series(minutes)
        assert s.value(60, "top") == pytest.approx(40 + 4 / 3)
        assert s.value(120, "bottom") == pytest.approx(42.0)
        assert s.get(120).phases == pytest.approx((2.0, 2.0, 2.0))

    def test_long_gaps_stay_empty(self):
        s = hd.Series([hd.Minute(0, 40.0, 40.0, None), hd.Minute(600, 41.0, 41.0, None)])
        assert s.get(300) is None

    def test_an_empty_series(self):
        assert hd.find_cycles([], 0) == []

    def test_an_open_cycle_is_flagged(self):
        minutes = [m for m in frame("regular") if m.t <= local("2026-11-16 13:44")]
        found = hd.find_cycles(minutes, minutes[-1].t + 60)
        assert len(found) == 1 and found[0].open


class TestLostMinutes:
    def _cycle(self, minutes=30):
        return hd.Cycle(0, (minutes - 1) * 60, False, 46, 42, 51, 1.0, 0.1, 10, False, True, "x")

    def test_a_regular_run_is_capped_at_the_hot_water_time(self):
        assert hd.lost_on_minutes(self._cycle(30), lambda t: True, "regular", 20) == 20

    def test_only_on_minutes_count(self):
        assert hd.lost_on_minutes(self._cycle(30), lambda t: t < 600, "regular", 20) == 10

    def test_a_boost_is_counted_whole(self):
        cycle = self._cycle(100)
        assert hd.lost_on_minutes(cycle, lambda t: True, "legionella_boost", 20) == 100

    def test_a_heater_run_loses_nothing(self):
        assert hd.lost_on_minutes(self._cycle(30), lambda t: True, "heater_run", 20) == 0


class TestStartLevel:
    def _c(self, bottom, slow=True):
        return hd.Cycle(0, 60, False, 46, bottom, 51, 1, 0, 10, False, slow, "bottom")

    def test_median_of_the_last_five_slow_starts(self):
        found = [self._c(b) for b in (40.0, 42.0, 42.4, 42.6, 43.0, 42.5)] + [self._c(30.0, False)]
        assert hd.start_level(found) == pytest.approx(42.5)

    def test_none_without_slow_starts(self):
        assert hd.start_level([self._c(42.0, False)]) is None

    def test_cost_uses_the_hour_price(self):
        minutes = frame("regular")
        found = hd.find_cycles(minutes, minutes[-1].t + 3600, price=lambda t: 0.10)
        assert found[0].eur == pytest.approx(found[0].kwh * 0.10)
