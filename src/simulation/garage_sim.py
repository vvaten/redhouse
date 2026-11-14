"""Offline garage simulation: model hindcast and policy runs on history.

The simulated garage (the plant) has its own cooling rates, fitted on
the measured history, so a run does not test the model against itself.
"""

import datetime
import random
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd

from src.control.garage_model import (
    SLOT_S,
    Decision,
    GarageParams,
    Slot,
    decide,
    plan,
    simulate,
)

HELSINKI = "Europe/Helsinki"
HORIZON_SLOTS = 96
PRICE_RELEASE_HOUR = 14
COLD_START_C = 8.0
COOLING_BINS = [0, 3, 6, 10, 15, 20, 30]


@dataclass(frozen=True)
class Plant:
    loss_k_per_h_per_k: float
    loss_k_per_h: float
    heat_gross_k_per_h: float
    noise_k: float = 0.02


def _cooling_hours(hourly: pd.DataFrame, min_hours: int) -> pd.DataFrame:
    """Hours inside runs of at least min_hours of falling temperature, with a
    one-hour step to the hour before (no data gap)."""
    df = hourly[["Autotalli", "air_temperature"]].dropna()
    step_ok = df.index.to_series().diff() == pd.Timedelta(hours=1)
    rate = -df["Autotalli"].diff()
    falling = (rate > 0) & step_ok
    run_id = (~falling).cumsum()
    run_len = falling.groupby(run_id).transform("sum")
    use = falling & (run_len >= min_hours)
    return pd.DataFrame({"diff": (df["Autotalli"] - df["air_temperature"])[use], "rate": rate[use]})


def fit_cooling(hourly: pd.DataFrame, min_hours: int = 6) -> tuple:
    """Median cooling rate per garage-outdoor difference bin, fitted as
    a * diff + b with each bin weighted by its hours. Returns (a, b, hours)."""
    hours = _cooling_hours(hourly, min_hours)
    groups = hours.groupby(pd.cut(hours["diff"], bins=COOLING_BINS), observed=True)
    medians = groups["rate"].median()
    counts = groups["rate"].size()
    if len(medians) < 2:
        raise ValueError("Too few cooling hours to fit")
    mids = np.array([interval.mid for interval in medians.index])
    a, b = np.polyfit(mids, medians.to_numpy(), 1, w=np.sqrt(counts.to_numpy()))
    return float(a), float(max(b, 0.0)), int(len(hours))


def hindcast(hourly: pd.DataFrame, params: GarageParams, hours: int = 12) -> pd.DataFrame:
    """Model against measured coasting windows that start under COLD_START_C."""
    df = hourly[["Autotalli", "air_temperature"]].dropna()
    values = df["Autotalli"].to_numpy()
    outs = df["air_temperature"].to_numpy()
    index = df.index
    rows = []
    i = 0
    while i + hours < len(df):
        window = values[i : i + hours + 1]
        span = (index[i + hours] - index[i]).total_seconds()
        coasting = all(window[k + 1] <= window[k] + 0.05 for k in range(hours))
        if span == hours * 3600 and coasting and window[0] < COLD_START_C:
            slots = [Slot(0, float(outs[i + k // 4]), None) for k in range(hours * 4)]
            predicted = simulate(float(window[0]), slots, [False] * len(slots), params)[-1]
            rows.append({"start": index[i], "actual": window[-1], "predicted": predicted})
            i += hours
        else:
            i += 1
    out = pd.DataFrame(rows)
    if not out.empty:
        out["error"] = out["predicted"] - out["actual"]
    return out


def price_known_until(now_utc: pd.Timestamp) -> pd.Timestamp:
    """Day-ahead prices: tomorrow's are known from 14:00 local, to its midnight."""
    local = now_utc.tz_convert(HELSINKI)
    days = 2 if local.hour >= PRICE_RELEASE_HOUR else 1
    end = (local.normalize().tz_localize(None) + pd.DateOffset(days=days)).tz_localize(HELSINKI)
    return end.tz_convert("UTC")


@dataclass(frozen=True)
class Inputs:
    """A winter as arrays, for fast slicing."""

    starts: np.ndarray
    prices: np.ndarray
    t_out: np.ndarray
    index: pd.DatetimeIndex


def inputs(quarter: pd.DataFrame) -> Inputs:
    idx = pd.DatetimeIndex(quarter.index)
    return Inputs(
        starts=np.array([int(t.timestamp()) for t in idx], dtype=np.int64),
        prices=quarter["price_total"].to_numpy(dtype=float),
        t_out=quarter["air_temperature"].to_numpy(dtype=float),
        index=idx,
    )


def make_slots(data: Inputs, i: int, bias_k: float) -> list:
    """The next HORIZON_SLOTS slots from row i, as the controller sees them."""
    known_until = int(price_known_until(data.index[i]).timestamp())
    stop = min(i + HORIZON_SLOTS, len(data.starts))
    return [
        Slot(
            int(data.starts[j]),
            float(data.t_out[j]) + bias_k,
            float(data.prices[j]) if data.starts[j] < known_until else None,
        )
        for j in range(i, stop)
    ]


def plant_step(t: float, t_out: float, on: bool, plant: Plant, rng: random.Random) -> float:
    hours = SLOT_S / 3600
    loss = (plant.loss_k_per_h_per_k * max(t - t_out, 0.0) + plant.loss_k_per_h) * hours
    if on:
        t += plant.heat_gross_k_per_h * hours - loss
    else:
        t = max(t - loss, min(t, t_out))
    return t + rng.gauss(0.0, plant.noise_k)


Policy = Callable[[int, float, int, list], Decision]


def model_policy(params: GarageParams, heater_kw: float) -> Policy:
    def policy(now: int, reading: float, reading_time: int, slots: list) -> Decision:
        p = plan(reading, slots, params, heater_kw)
        return decide(now, reading, reading_time, p, False, params)

    return policy


def thermostat_policy(setpoint_c: float) -> Policy:
    def policy(now: int, reading: float, reading_time: int, slots: list) -> Decision:
        return Decision(reading < setpoint_c, "thermostat")

    return policy


def off_policy() -> Policy:
    def policy(now: int, reading: float, reading_time: int, slots: list) -> Decision:
        return Decision(False, "off")

    return policy


def run_policy(
    quarter: pd.DataFrame,
    policy: Policy,
    plant: Plant,
    start_c: float,
    bias_k: float = 0.0,
    seed: int = 1,
    stale_slots: Optional[range] = None,
) -> pd.DataFrame:
    """One row per slot: plant temperature, heater state, reason, price."""
    data = inputs(quarter)
    rng = random.Random(seed)
    t = start_c
    reading, reading_time = round(t, 1), int(data.starts[0])
    rows = []
    for i in range(len(data.starts) - HORIZON_SLOTS):
        now = int(data.starts[i])
        if stale_slots is None or i not in stale_slots:
            reading, reading_time = round(t, 1), now
        decision = policy(now, reading, reading_time, make_slots(data, i, bias_k))
        rows.append((data.index[i], t, decision.on, decision.reason, data.prices[i], data.t_out[i]))
        t = plant_step(t, float(data.t_out[i]), decision.on, plant, rng)
    frame = pd.DataFrame(rows, columns=["time", "garage_c", "on", "reason", "price", "t_out"])
    return frame.set_index("time")


def metrics(run: pd.DataFrame, heater_kw: float) -> dict:
    if run["price"].isna().any():
        raise ValueError("A slot without a price in the run")
    kwh = run["on"] * heater_kw * SLOT_S / 3600
    local_hour = pd.DatetimeIndex(run.index).tz_convert(HELSINKI).hour
    day = (local_hour >= 7) & (local_hour < 22)
    return {
        "hours_under_1c": float((run["garage_c"] < 1.0).sum() * SLOT_S / 3600),
        "hours_under_2c": float((run["garage_c"] < 2.0).sum() * SLOT_S / 3600),
        "min_c": float(run["garage_c"].min()),
        "median_c": float(run["garage_c"].median()),
        "kwh": float(kwh.sum()),
        "cost_eur": float((kwh * run["price"]).sum()),
        "day_share_of_kwh": float(kwh[day].sum() / kwh.sum()) if kwh.sum() else 0.0,
        "switches": int((run["on"].astype(int).diff().abs() > 0).sum()),
    }


def winter(quarter: pd.DataFrame, year: int) -> pd.DataFrame:
    """Nov 1 of year to Mar 31 of the next year on a full 15-min grid, gaps interpolated."""
    start = pd.Timestamp(datetime.datetime(year, 11, 1), tz="UTC")
    stop = pd.Timestamp(datetime.datetime(year + 1, 4, 1), tz="UTC")
    grid = pd.date_range(start, stop, freq="15min", inclusive="left")
    part = quarter[["price_total", "air_temperature"]].reindex(grid)
    return pd.DataFrame(part.interpolate(limit_direction="both"))
