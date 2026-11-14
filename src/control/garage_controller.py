"""Garage heater controller: one tick every 15 minutes.

Reads the garage sensor, plans the next 24 h on prices and the outdoor
forecast, and sets both heater relays. The plan and the decision come
from garage_model, which the offline simulation also runs.
"""

import datetime
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from influxdb_client import Point

from src.common.local_time import HELSINKI
from src.common.logger import setup_logger
from src.control.garage_model import (
    SLOT_S,
    Decision,
    GarageParams,
    Plan,
    Slot,
    decide,
    plan,
    plausible,
)
from src.data_collection.shelly_ht_server import read_states

logger = setup_logger(__name__)

LOAD_ID = "garage_heater"
HORIZON_SLOTS = 96
# Gen1 treats timer 0 as no flip-back; the design allows 75 min of OFF at most
MAX_OFF_TIMER_S = 4500
REPO = Path(__file__).resolve().parents[2]
REQUIRED_KEYS = (
    "relay_urls",
    "heater_kw",
    "sensor_name",
    "sensor_status_file",
    "target_c",
    "floor_c",
    "stale_after_s",
    "off_timer_s",
    "loss_k_per_h_per_k",
    "loss_k_per_h",
    "heat_gross_k_per_h",
)
# Environment overrides of the yaml values
ENV_OVERRIDES = {
    "target_c": "GARAGE_TARGET_C",
    "floor_c": "GARAGE_FLOOR_C",
    "sensor_status_file": "GARAGE_SENSOR_STATUS_FILE",
}


@dataclass(frozen=True)
class GarageSettings:
    relay_urls: tuple
    heater_kw: tuple
    sensor_name: str
    status_file: Path
    off_timer_s: int
    params: GarageParams

    @property
    def total_kw(self) -> float:
        return float(sum(self.heater_kw))


@dataclass(frozen=True)
class TickResult:
    decision: Decision
    reading_c: Optional[float]
    reading_age_s: Optional[float]
    plan: Optional[Plan]
    relay_states: tuple
    success: bool


def load_settings(section: dict) -> GarageSettings:
    """Settings from the config.yaml garage section; a missing key is an error."""
    missing = [k for k in REQUIRED_KEYS if k not in section]
    if missing:
        raise ValueError(f"config.yaml garage section lacks {missing}")
    values = dict(section)
    for key, env in ENV_OVERRIDES.items():
        if env in os.environ:
            logger.warning(f"garage {key} overridden by {env}={os.environ[env]}")
            values[key] = os.environ[env]
    if len(values["relay_urls"]) != len(values["heater_kw"]):
        raise ValueError("garage relay_urls and heater_kw differ in length")
    status_file = Path(values["sensor_status_file"])
    params = GarageParams(
        target_c=float(values["target_c"]),
        floor_c=float(values["floor_c"]),
        stale_after_s=float(values["stale_after_s"]),
        loss_k_per_h_per_k=float(values["loss_k_per_h_per_k"]),
        loss_k_per_h=float(values["loss_k_per_h"]),
        heat_gross_k_per_h=float(values["heat_gross_k_per_h"]),
    )
    if params.floor_c >= params.target_c:
        raise ValueError("garage floor_c must be under target_c")
    off_timer_s = int(values["off_timer_s"])
    if not 2 * SLOT_S < off_timer_s <= MAX_OFF_TIMER_S:
        raise ValueError(
            f"garage off_timer_s must be over {2 * SLOT_S} and at most {MAX_OFF_TIMER_S}"
        )
    return GarageSettings(
        relay_urls=tuple(values["relay_urls"]),
        heater_kw=tuple(float(kw) for kw in values["heater_kw"]),
        sensor_name=str(values["sensor_name"]),
        status_file=status_file if status_file.is_absolute() else REPO / status_file,
        off_timer_s=off_timer_s,
        params=params,
    )


def read_reading(
    status_file: Path, sensor_name: str, name_of: Callable[[str], Optional[str]]
) -> tuple:
    """(temperature, report time) of the named sensor, the newest report if
    several ids map to the name, or (None, None)."""
    matches = [s for i, s in read_states(status_file).items() if name_of(i) == sensor_name]
    if not matches:
        logger.error(f"No sensor named {sensor_name} in {status_file}")
        return None, None
    newest = max(matches, key=lambda s: s["updated"])
    if "temp" not in newest or newest["temp"] is None:
        logger.error(f"Sensor {sensor_name} has no temperature")
        return None, None
    return float(newest["temp"]), float(newest["updated"])


def slot_start(now: float) -> int:
    return int(now - now % SLOT_S)


def build_slots(start: int, prices: dict, outdoor: dict) -> list:
    """HORIZON_SLOTS slots from start. A slot without a forecast carries the
    previous one on; with no forecast at all there is no plan."""
    slots = []
    earlier = [t for t in outdoor if t < start]
    last_out: Optional[float] = outdoor[max(earlier)] if earlier else None
    for i in range(HORIZON_SLOTS):
        t = start + i * SLOT_S
        if t in outdoor:
            last_out = outdoor[t]
        if last_out is None:
            continue
        slots.append(Slot(t, last_out, prices[t] if t in prices else None))
    return slots


def _rfc3339(epoch: int) -> str:
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _query_series(
    influx, bucket: str, measurement: str, field: str, start: int, hours: int
) -> dict:
    """15-min means of one field as {slot start epoch: value}, from an hour before start."""
    flux = f"""
        from(bucket: "{bucket}")
          |> range(start: {_rfc3339(start - 3600)}, stop: {_rfc3339(start + hours * 3600)})
          |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "{field}")
          |> aggregateWindow(every: 15m, fn: mean, createEmpty: false, timeSrc: "_start")
    """
    series = {}
    for table in influx.query_with_retry(flux):
        for record in table.records:
            if record.get_value() is not None:
                series[int(record.get_time().timestamp())] = float(record.get_value())
    return series


def fetch_inputs(influx, config, start: int) -> tuple:
    """(prices, outdoor) for the horizon. A failed query gives an empty dict."""
    try:
        prices = _query_series(
            influx, config.influxdb_bucket_spotprice, "spot", "price_total", start, 25
        )
    except Exception as e:
        logger.error(f"Prices unavailable: {e}")
        prices = {}
    try:
        outdoor = _query_series(
            influx, config.influxdb_bucket_weather, "weather", "Air temperature", start, 25
        )
    except Exception as e:
        logger.error(f"Outdoor forecast unavailable: {e}")
        outdoor = {}
    return prices, outdoor


def tick(
    now: float, settings: GarageSettings, reading: tuple, prices: dict, outdoor: dict
) -> tuple:
    """(decision, plan or None) for this tick; no IO."""
    reading_c, reading_time = reading
    slots = build_slots(slot_start(now), prices, outdoor)
    garage_plan = None
    if plausible(reading_c) and slots and slots[0].start == slot_start(now):
        garage_plan = plan(reading_c, slots, settings.params, settings.total_kw)
    decision = decide(int(now), reading_c, reading_time, garage_plan, False, settings.params)
    return decision, garage_plan


def apply(decision: Decision, relays: list, off_timer_s: int) -> tuple:
    """Command every relay; a relay that fails does not stop the others.
    Returns each relay's state after the command (None: no answer)."""
    states = []
    for relay in relays:
        state = relay.set(decision.on, off_timer_s)
        if state is None or state != decision.on:
            logger.error(f"Garage relay {relay.url} is {state}, wanted {decision.on}")
        states.append(state)
    return tuple(states)


def build_point(now: float, settings: GarageSettings, result: TickResult) -> Point:
    local_date = datetime.datetime.fromtimestamp(now, HELSINKI).date().isoformat()
    on_kw = sum(kw for kw, s in zip(settings.heater_kw, result.relay_states) if s)
    point = (
        Point("load_control")
        .tag("program_date", local_date)
        .tag("load_id", LOAD_ID)
        .tag("data_type", "actual")
        .field("command", "ON" if result.decision.on else "OFF")
        .field("power_kw", float(on_kw))
        .field("reason", result.decision.reason)
        .field("success", result.success)
        .time(datetime.datetime.fromtimestamp(now, datetime.timezone.utc))
    )
    for i, state in enumerate(result.relay_states, start=1):
        point = point.field(f"relay{i}_on", -1 if state is None else int(state))
    if result.reading_c is not None:
        point = point.field("reading_c", float(result.reading_c))
    if result.reading_age_s is not None:
        point = point.field("reading_age_s", float(result.reading_age_s))
    if result.plan is not None:
        point = point.field("plan_min_c", float(result.plan.min_c))
        point = point.field("plan_on_slots", int(sum(result.plan.on)))
    return point


def run_once(now: float, settings: GarageSettings, relays: list, io: "GarageIO") -> TickResult:
    """One tick with its IO. The lock is held only while the relays switch."""
    reading = io.read_reading(settings)
    prices, outdoor = io.fetch_inputs(slot_start(now))
    decision, garage_plan = tick(now, settings, reading, prices, outdoor)
    with io.lock():
        states = apply(decision, relays, settings.off_timer_s)
    success = all(s == decision.on for s in states)
    age = None if reading[1] is None else now - reading[1]
    result = TickResult(decision, reading[0], age, garage_plan, states, success)
    logger.info(
        f"Garage {result.reading_c} C (age {age}), {decision.reason}: "
        f"{'ON' if decision.on else 'OFF'}, relays {states}"
    )
    io.write(build_point(now, settings, result))
    return result


@dataclass
class GarageIO:
    """The outside world of one tick, replaceable in tests."""

    read_reading: Callable
    fetch_inputs: Callable
    lock: Callable
    write: Callable
