"""Tests for the power budget in the program generator."""

import datetime
import json
from unittest.mock import Mock

import pandas as pd
import pytest

from src.common.local_time import HELSINKI
from src.control import load_budget as lb
from src.control.heating_optimizer import HeatingOptimizer
from src.control.power_cap import CapSettings

CAP = CapSettings(0.8, 22, 7, 7.5, 0.3, 0.1, 1.97, 34.5)
SECTION = {
    "enabled": True,
    "base_kw": 1.6,
    "battery_allowance_kw": 1.5,
    "pump_on_kw": 3.4,
    "sauna_kw": 7.0,
    "sauna_share": 0.5,
    "sauna_weekdays": [5],
    "sauna_start_hour": 17,
    "sauna_end_hour": 22,
}
SETTINGS = lb.load_budget_settings(SECTION)


def _local(y, mo, d, h):
    return HELSINKI.localize(datetime.datetime(y, mo, d, h))


def _day(date, prices):
    index = pd.DatetimeIndex([_local(*date, h) for h in range(24)])
    return pd.DataFrame({"heating_prio": prices}, index=index)


def test_saturday_evening_reserves_the_sauna():
    assert lb.fixed_kw(_local(2026, 11, 14, 18), SETTINGS) == pytest.approx(3.1 + 3.5)
    assert lb.fixed_kw(_local(2026, 11, 14, 16), SETTINGS) == pytest.approx(3.1)
    assert lb.fixed_kw(_local(2026, 11, 13, 18), SETTINGS) == pytest.approx(3.1)


def test_night_hours_have_more_headroom():
    day = lb.headroom_kw(_local(2026, 11, 13, 12), 7.7, CAP, SETTINGS)
    night = lb.headroom_kw(_local(2026, 11, 13, 23), 7.7, CAP, SETTINGS)
    assert night == pytest.approx(7.7 / 0.8 - 3.1)
    assert night > day


def test_the_sauna_hours_go_last_and_the_count_stays():
    """The sauna evening is the cheapest, but the pump does not fit under the cap there."""
    prices = [0.30] * 24
    for h in (17, 18, 19, 20, 21):
        prices[h] = 0.01
    day = _day((2026, 11, 14), prices)
    budgeted = lb.apply_budget(day, 7.7, CAP, SETTINGS)
    assert budgeted["over_cap"].sum() == 5
    selected = HeatingOptimizer().select_cheapest_hours(budgeted, 6)
    assert len(selected) == 6
    assert not any(t.hour in (17, 18, 19, 20, 21) for t in selected.index)
    assert (budgeted["heating_prio"] == day["heating_prio"]).all()


def test_an_over_cap_hour_is_used_when_the_day_needs_it():
    day = _day((2026, 11, 14), [0.10] * 24)
    budgeted = lb.apply_budget(day, 7.7, CAP, SETTINGS)
    assert len(HeatingOptimizer().select_cheapest_hours(budgeted, 22)) == 22


def _config(enabled=True):
    config = Mock(load_budget_section={**SECTION, "enabled": enabled})
    config.power_cap_section = {
        "night_factor": 0.8,
        "night_start_hour": 22,
        "night_end_hour": 7,
        "month_floor_kw": 7.5,
        "plan_margin_kw": 0.3,
        "guard_margin_kw": 0.1,
        "tariff_eur_per_kw": 1.97,
        "max_import_kw": 34.5,
    }
    return config


def _program_date(y, mo, d):
    return datetime.datetime(y, mo, d)


def test_without_the_budget_the_frame_is_left_alone(tmp_path):
    day = _day((2026, 11, 14), [0.10] * 24)
    same = lb.budget_priorities(
        day, _config(False), tmp_path / "s.json", _program_date(2026, 11, 14)
    )
    assert same is day
    assert "over_cap" not in same


def test_a_missing_state_plans_at_the_floor(tmp_path):
    day = _day((2026, 11, 14), [0.10] * 24)
    budgeted = lb.budget_priorities(
        day, _config(), tmp_path / "none.json", _program_date(2026, 11, 14)
    )
    assert budgeted["over_cap"].sum() == 5


def test_a_high_cap_leaves_room_for_the_sauna_evening(tmp_path):
    state = tmp_path / "s.json"
    state.write_text(json.dumps({"month": "2026-11", "cap_kw": 12.0}))
    day = _day((2026, 11, 14), [0.10] * 24)
    budgeted = lb.budget_priorities(day, _config(), state, _program_date(2026, 11, 14))
    assert budgeted["over_cap"].sum() == 0


def test_a_new_month_starts_at_the_floor(tmp_path):
    state = tmp_path / "s.json"
    state.write_text(json.dumps({"month": "2026-11", "cap_kw": 12.0}))
    day = _day((2026, 12, 5), [0.10] * 24)
    budgeted = lb.budget_priorities(day, _config(), state, _program_date(2026, 12, 5))
    assert budgeted["over_cap"].sum() == 5


def test_a_naive_index_is_refused():
    day = pd.DataFrame({"heating_prio": [0.1]}, index=pd.DatetimeIndex(["2026-11-14 18:00"]))
    with pytest.raises(ValueError):
        lb.apply_budget(day, 7.7, CAP, SETTINGS)
