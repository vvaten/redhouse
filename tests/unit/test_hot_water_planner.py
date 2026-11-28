"""The boost in the day plan: the budget share and the planner link."""

import datetime
import json

import pandas as pd
import pytest

from src.common.local_time import HELSINKI
from src.control import hot_water_boost as hb
from src.control import hot_water_planner as hp
from src.control import load_budget as lb
from src.control.heating_optimizer import HeatingOptimizer
from tests.unit.test_load_budget import HOT_WATER_OFF, SECTION, _config, _day

BOOST = hb.load_boost_settings(HOT_WATER_OFF["boost"])
# Fri 2026-12-11 22:00 local
EXPIRY = hb.parse_utc("2026-12-11T20:00:00Z")


def hour(text: str) -> datetime.datetime:
    return HELSINKI.localize(datetime.datetime.strptime(text, "%Y-%m-%d %H"))


class TestBudgetShare:
    def test_off_adds_nothing(self):
        assert hp.boost_budget(HOT_WATER_OFF) is None
        assert hp.boost_share_kw(hour("2026-12-11 22"), None) == 0.0

    def test_the_slot_hours_carry_the_heater_share(self):
        budget = hp.BoostBudget(6.5, BOOST)
        assert hp.boost_share_kw(hour("2026-12-11 22"), budget) == pytest.approx(6.5)
        assert hp.boost_share_kw(hour("2026-12-11 23"), budget) == pytest.approx(6.5)
        assert hp.boost_share_kw(hour("2026-12-12 00"), budget) == 0.0
        assert hp.boost_share_kw(hour("2026-12-11 21"), budget) == 0.0

    def test_boost_budget_raises_only_the_slot_hours_fixed_load(self):
        on = lb.load_budget_settings(SECTION, {**HOT_WATER_OFF, "boost_budget": True})
        off = lb.load_budget_settings(SECTION, HOT_WATER_OFF)
        for h in range(18, 24):
            t = hour(f"2026-12-11 {h:02d}")
            extra = 6.5 if h in (22, 23) else 0.0
            assert lb.fixed_kw(t, on) == pytest.approx(lb.fixed_kw(t, off) + extra)

    def test_a_missing_switch_is_an_error(self):
        with pytest.raises(ValueError):
            hp.boost_budget({"boost_kw": 6.5, "boost": HOT_WATER_OFF["boost"]})


class TestPlannerLink:
    def _day(self):
        prices = [0.30] * 24
        for h in (22, 23):
            prices[h] = 0.01
        return _day((2026, 12, 11), prices)

    def test_both_off_leave_the_priorities_identical(self, tmp_path):
        day = self._day()
        same = lb.budget_priorities(
            day, _config(enabled=False), tmp_path / "cap.json", day.index[0]
        )
        assert same is day

    def test_the_slot_hours_go_last_and_the_count_stays(self):
        linked = hp.slot_last(self._day(), BOOST)
        assert linked["over_cap"].sum() == 2
        selected = HeatingOptimizer().select_cheapest_hours(linked, 6)
        assert len(selected) == 6
        assert not any(t.hour in (22, 23) for t in selected.index)

    def test_the_link_joins_an_existing_budget(self):
        day = self._day()
        day["over_cap"] = [h == 5 for h in range(24)]
        linked = hp.slot_last(day, BOOST)
        assert linked["over_cap"].sum() == 3

    def test_a_naive_index_is_refused(self):
        day = pd.DataFrame(
            {"heating_prio": [1.0]}, index=pd.DatetimeIndex([datetime.datetime(2026, 12, 11)])
        )
        with pytest.raises(ValueError):
            hp.slot_last(day, BOOST)


class TestLostMinutes:
    ON = {**HOT_WATER_OFF, "planner_link": True}

    def test_off_gives_nothing(self, tmp_path):
        assert (
            hp.lost_minutes(HOT_WATER_OFF, "2026-12-02", lambda s: [(1, 9)], 100, tmp_path / "g")
            == 0
        )

    def test_lost_minutes_are_added_once(self, tmp_path):
        path = tmp_path / "given.json"
        cycles = lambda since: [(1000, 12), (2000, 0), (3000, 8)]  # noqa: E731
        assert hp.lost_minutes(self.ON, "2026-12-02", cycles, 5000, path) == 20
        assert hp.lost_minutes(self.ON, "2026-12-03", cycles, 5000, path) == 0
        assert json.loads(path.read_text())["2026-12-02"] == [1000, 3000]

    def test_a_second_run_for_the_same_day_gives_them_again(self, tmp_path):
        path = tmp_path / "given.json"
        cycles = lambda since: [(1000, 12)]  # noqa: E731
        assert hp.lost_minutes(self.ON, "2026-12-02", cycles, 5000, path) == 12
        assert hp.lost_minutes(self.ON, "2026-12-02", cycles, 5000, path) == 12

    def test_a_read_failure_gives_nothing(self):
        class Config:
            hot_water_section = TestLostMinutes.ON
            influxdb_bucket_load_control = "load_control"

        class Broken:
            def query_with_retry(self, flux):
                raise TimeoutError("down")

        assert hp.lost_heating_hours(Config(), Broken(), "2026-12-02") == 0.0
