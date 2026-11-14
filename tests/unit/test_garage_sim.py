"""Tests for the offline garage simulator."""

import random

import pandas as pd
import pytest

from src.control.garage_model import GarageParams
from src.simulation import garage_sim as gs

PARAMS = GarageParams(5.0, 2.0, 10800, 0.010, 0.08, 0.6)


def _quarter(days, t_out, price=0.10, start="2025-01-06 00:00"):
    index = pd.date_range(start, periods=days * 96, freq="15min", tz="UTC")
    return pd.DataFrame({"price_total": price, "air_temperature": t_out}, index=index)


def _cooling(a, b, diffs, hours=10):
    """Hourly coasting runs that cool at a * diff + b from each starting difference."""
    parts = []
    start = pd.Timestamp("2024-01-01", tz="UTC")
    for diff in diffs:
        index = pd.date_range(start, periods=hours, freq="h")
        garage = [5.0 - (a * diff + b) * k for k in range(hours)]
        parts.append(
            pd.DataFrame({"Autotalli": garage, "air_temperature": 5.0 - diff}, index=index)
        )
        start += pd.Timedelta(hours=hours + 1)
    return pd.concat(parts)


def test_the_cooling_fit_recovers_slope_and_intercept():
    a, b, n = gs.fit_cooling(_cooling(0.01, 0.05, [2, 4, 8, 12, 18, 25]))
    assert n > 0
    assert a == pytest.approx(0.01, abs=0.003)
    assert b == pytest.approx(0.05, abs=0.03)


def test_the_cooling_fit_skips_hours_across_a_data_gap():
    df = _cooling(0.01, 0.05, [10, 20])
    hours = gs._cooling_hours(df, 6)
    gap_start = df.index[10]
    assert gap_start not in hours.index


def test_the_hindcast_uses_only_cold_windows():
    warm = _cooling(0.01, 0.05, [10], hours=30)
    cold = warm.assign(Autotalli=warm["Autotalli"] - 5.0)
    assert gs.hindcast(warm.assign(Autotalli=warm["Autotalli"] + 10), PARAMS).empty
    hind = gs.hindcast(cold, PARAMS)
    assert len(hind) == 2
    assert set(hind.columns) >= {"actual", "predicted", "error"}


@pytest.mark.parametrize(
    "now_utc, expected_local_end",
    [
        ("2025-01-06 11:59", "2025-01-07 00:00"),
        ("2025-01-06 12:00", "2025-01-08 00:00"),
        # Spring clock change: the next midnight is at +03 after it
        ("2026-03-28 13:00", "2026-03-30 00:00"),
        # Autumn clock change
        ("2025-10-25 13:00", "2025-10-27 00:00"),
    ],
)
def test_prices_are_known_to_local_midnight(now_utc, expected_local_end):
    end = gs.price_known_until(pd.Timestamp(now_utc, tz="UTC")).tz_convert(gs.HELSINKI)
    assert end.strftime("%Y-%m-%d %H:%M") == expected_local_end


def test_slots_past_the_known_prices_have_no_price():
    slots = gs.make_slots(gs.inputs(_quarter(3, -10.0)), 0, 0.0)
    assert slots[0].price is not None
    assert any(s.price is None for s in slots)


def test_the_forecast_bias_moves_the_outdoor_value():
    assert gs.make_slots(gs.inputs(_quarter(2, -10.0)), 0, 2.0)[0].t_out_c == pytest.approx(-8.0)


def test_the_model_policy_holds_the_floor_in_a_cold_week():
    plant = gs.Plant(0.007, 0.05, 0.8, noise_k=0.0)
    run = gs.run_policy(_quarter(5, -20.0), gs.model_policy(PARAMS, 2.13), plant, 4.0)
    assert run["garage_c"].min() >= 1.9
    assert run["on"].any()


def test_relays_off_lets_the_garage_freeze():
    plant = gs.Plant(0.007, 0.05, 0.8, noise_k=0.0)
    run = gs.run_policy(_quarter(5, -20.0), gs.off_policy(), plant, 4.0)
    assert run["garage_c"].min() < 1.0


def test_a_stale_reading_turns_the_heat_on_after_three_hours():
    plant = gs.Plant(0.007, 0.05, 0.8, noise_k=0.0)
    run = gs.run_policy(
        _quarter(3, 3.0), gs.model_policy(PARAMS, 2.13), plant, 6.0, stale_slots=range(10, 40)
    )
    stale = run.iloc[10:40]
    first = stale.index[stale["reason"] == "stale_reading"][0]
    assert first - stale.index[0] >= pd.Timedelta(hours=3)


def test_metrics_count_energy_and_cold_hours():
    index = pd.date_range("2025-01-06", periods=8, freq="15min", tz="UTC")
    run = pd.DataFrame(
        {"garage_c": [0.5] * 4 + [3.0] * 4, "on": [True] * 4 + [False] * 4, "price": 0.2},
        index=index,
    )
    m = gs.metrics(run, 2.0)
    assert m["hours_under_1c"] == pytest.approx(1.0)
    assert m["kwh"] == pytest.approx(2.0)
    assert m["cost_eur"] == pytest.approx(0.4)
    assert m["switches"] == 1


def test_metrics_refuse_a_slot_without_a_price():
    index = pd.date_range("2025-01-06", periods=2, freq="15min", tz="UTC")
    run = pd.DataFrame({"garage_c": 3.0, "on": True, "price": [0.1, None]}, index=index)
    with pytest.raises(ValueError):
        gs.metrics(run, 2.0)


def test_a_winter_is_a_full_grid_with_gaps_filled():
    q = _quarter(200, -5.0, start="2024-10-01")
    q = q.drop(q.index[(q.index >= "2024-12-01") & (q.index < "2024-12-02")])
    w = gs.winter(q, 2024)
    assert len(w) == 151 * 96
    assert not w.isna().any().any()


def test_the_plant_noise_is_repeatable_with_a_seed():
    plant = gs.Plant(0.007, 0.05, 0.8, noise_k=0.05)
    a = gs.plant_step(3.0, -5.0, False, plant, random.Random(3))
    b = gs.plant_step(3.0, -5.0, False, plant, random.Random(3))
    assert a == b
