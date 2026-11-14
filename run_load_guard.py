#!/usr/bin/env python
"""Load guard tick - wrapper script for systemd, every minute.

Reads the Shelly 3EM over HTTP (not InfluxDB, so it works while the
database is down) and projects the hour's counted import. In mode
dry_run it logs what it would shed; in mode act it sheds.

Usage:
    python run_load_guard.py
"""

import datetime
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests
from influxdb_client import Point

from src.common.atomic_json import write_json
from src.common.config import get_config
from src.common.control_lock import ControlLockTimeout, control_lock
from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger
from src.control import garage_controller as gc
from src.control import load_guard as lg
from src.control import load_overrides as lo
from src.control import power_cap as pc
from src.control.hardware_implementations import GarageRelay, MockGarageRelay
from src.control.pump_controller import PumpController

logger = setup_logger(__name__, "load_guard.log")

DATA = Path(__file__).resolve().parent / "data"
MINUTE_LOG = DATA / "guard_minutes.json"
CAP_STATE = DATA / "power_cap_state.json"
GARAGE_STATE = DATA / "garage_state.json"
KEEP_S = 70 * 60
HTTP_TIMEOUT_S = 5
INFLUX_TIMEOUT_MS = 5_000
# A garage state older than this is not trusted
STATE_MAX_AGE_S = 1800
OVERRIDES = DATA / "load_overrides.json"
TEST_LIMIT_ENV = "LOAD_GUARD_TEST_LIMIT_KW"
MODES = ("dry_run", "act")


def read_meter(url: str, now: float) -> lg.Sample:
    response = requests.get(f"{url}/status", timeout=HTTP_TIMEOUT_S)
    response.raise_for_status()
    meters = response.json()["emeters"]
    if len(meters) != 3:
        raise ValueError(f"expected 3 phases, got {len(meters)}")
    phase_kw = tuple(float(m["power"]) / 1000 for m in meters)
    net_wh = sum(float(m["total"]) - float(m["total_returned"]) for m in meters)
    import_wh = sum(float(m["total"]) for m in meters)
    return lg.Sample(now, (phase_kw[0], phase_kw[1], phase_kw[2]), net_wh, import_wh)


def load_minutes(now: float) -> list:
    try:
        rows = json.loads(MINUTE_LOG.read_text())
    except (OSError, ValueError):
        return []
    samples = []
    for row in rows if isinstance(rows, list) else []:
        try:
            t, p1, p2, p3, net_wh, import_wh = (float(v) for v in row)
        except (TypeError, ValueError):
            continue
        if now - KEEP_S <= t < now:
            samples.append(lg.Sample(t, (p1, p2, p3), net_wh, import_wh))
    return sorted(samples, key=lambda s: s.t)


def save_minutes(samples: list) -> None:
    rows = [[s.t, *s.phase_kw, s.net_wh, s.import_wh] for s in samples]
    write_json(MINUTE_LOG, rows)


def _json_state(path: Path) -> Optional[dict]:
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def garage_sheddable(now: float) -> bool:
    """The garage heats for its plan and is not held: under the floor or on
    a stale reading it heats on purpose, and a held garage is already off."""
    state = _json_state(GARAGE_STATE)
    if state is None or any(k not in state for k in ("on", "t", "reason")):
        logger.warning(f"{GARAGE_STATE} lacks on/t/reason: garage not shed")
        return False
    if not isinstance(state["on"], bool) or not isinstance(state["t"], (int, float)):
        logger.warning(f"{GARAGE_STATE} has bad values: garage not shed")
        return False
    if lo.active_override(OVERRIDES, lo.GARAGE, now) is not None:
        return False
    fresh = now - float(state["t"]) < STATE_MAX_AGE_S
    return state["on"] and fresh and state["reason"] == "plan_heat"


def pump_command(path: Path) -> Optional[str]:
    state = _json_state(path)
    if state is None or "last_command" not in state:
        logger.warning(f"{path} lacks last_command: the pump is not shed")
        return None
    return state["last_command"]


def _staging(config) -> bool:
    flag = os.getenv("STAGING_MODE", "false").lower() in ("true", "1", "yes")
    return flag or config.influxdb_bucket_load_control.endswith("_staging")


def guard_settings(section: dict, garage_kw: float) -> tuple:
    """(mode, GuardSettings) from the config.yaml load_guard section."""
    keys = ("mode", "pump_ale_kw", "pump_evu_kw")
    missing = [k for k in keys if k not in section]
    if missing:
        raise ValueError(f"config.yaml load_guard section lacks {missing}")
    if section["mode"] not in MODES:
        raise ValueError(f"load_guard mode must be one of {MODES}")
    settings = lg.GuardSettings(
        garage_kw, float(section["pump_ale_kw"]), float(section["pump_evu_kw"])
    )
    return section["mode"], settings


def _record(hold: lo.Override, now: float) -> None:
    kept = [
        o for o in lo.read_overrides(OVERRIDES) if o.load_id != hold.load_id or not o.active(now)
    ]
    lo.write_overrides(OVERRIDES, kept + [hold], now)


def _forget(hold: lo.Override, now: float) -> None:
    kept = [o for o in lo.read_overrides(OVERRIDES) if o != hold]
    lo.write_overrides(OVERRIDES, kept, now)


def _shed_garage(relays: list, off_timer_s: int, now: float, until: float, reason: str) -> bool:
    """The hold is written before the relays switch, so no switch goes unrecorded."""
    if lo.active_override(OVERRIDES, lo.GARAGE, now) is not None:
        return False
    hold = lo.Override(lo.GARAGE, "OFF", now, until, reason)
    _record(hold, now)
    states = [relay.set(False, off_timer_s) for relay in relays]
    if all(state is False for state in states):
        return True
    # A relay that stays ON is no shed; the flip-back timer ends any OFF one
    logger.error(f"Garage shed failed, relays {states}")
    _forget(hold, now)
    return False


def _shed_pump(pump, command: str, now: float, until: float, reason: str) -> bool:
    if pump.last_command == command:
        return False
    hold = lo.Override(lo.PUMP, command, now, until, reason, pump.last_command)
    _record(hold, now)
    result = pump.execute_command(command, int(now), int(now))
    if result["success"]:
        return True
    logger.error(f"Pump shed to {command} failed: {result['error']}")
    _forget(hold, now)
    return False


def act(verdict: lg.Verdict, now: float, garage_relays: list, off_timer_s: int, make_pump) -> list:
    """Shed for real under the lock, each shed held to the hour's end.
    Returns the loads shed; a busy lock raises ControlLockTimeout."""
    until = lg.hour_start(now) + 3600
    reason = f"cap_guard_{verdict.step_class or 'unknown'}"
    loads = {s.load for s in verdict.sheds}
    pump_cmd = "EVU" if "pump_evu" in loads else "ALE" if "pump_ale" in loads else None
    done = []
    with control_lock(0):
        if "garage_off" in loads and _shed_garage(garage_relays, off_timer_s, now, until, reason):
            done.append("garage_off")
        # The pump state file is read here, under the lock
        if pump_cmd is not None and _shed_pump(make_pump(), pump_cmd, now, until, reason):
            done.append(f"pump_{pump_cmd.lower()}")
    return done


def verdict_point(
    verdict: lg.Verdict, now: float, sample: lg.Sample, mode: str, done: list
) -> Point:
    return (
        Point("load_guard")
        .tag("data_type", "actual" if mode == "act" else "dry_run")
        .field("over", verdict.over)
        .field("done", ",".join(done))
        .field("projected_kw", float(verdict.projected_kw))
        .field("counted_kw", float(verdict.counted_kw))
        .field("limit_kw", float(verdict.limit_kw))
        .field("total_kw", float(sample.total_kw))
        .field("step_class", verdict.step_class or "")
        .field("sheds", ",".join(s.load for s in verdict.sheds))
        .time(datetime.datetime.fromtimestamp(now, datetime.timezone.utc))
    )


def _act(config, verdict: lg.Verdict, now: float, staging: bool) -> list:
    garage = gc.load_settings(config.garage_section)
    relay_type = MockGarageRelay if staging else GarageRelay
    relays = [relay_type(url) for url in garage.relay_urls]
    try:
        done = act(
            verdict, now, relays, garage.off_timer_s, lambda: PumpController(dry_run=staging)
        )
    except ControlLockTimeout:
        logger.warning("Control lock busy: nothing shed this minute")
        return []
    except OSError as e:
        logger.error(f"Shed stopped, the hold could not be written: {e}")
        return []
    logger.warning(f"Shed {done}")
    return done


def main() -> int:
    config = get_config()
    settings = pc.load_cap_settings(config.power_cap_section)
    now = time.time()
    try:
        sample = read_meter(config.shelly_em3_url, now)
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        logger.error(f"3EM unreadable, no guard this minute: {e}")
        return 1
    samples = load_minutes(now) + [sample]
    try:
        save_minutes(samples)
    except OSError as e:
        logger.error(f"Minute log not saved, the guard goes on without it: {e}")
    hour = lg.utc(lg.hour_start(now))
    cap_kw = pc.read_cap_kw(CAP_STATE, hour, settings)
    limit_kw = pc.guard_limit_kw(cap_kw, settings)
    if TEST_LIMIT_ENV in os.environ:
        # Only for the supervised cutover test: a low limit makes the guard act
        limit_kw = float(os.environ[TEST_LIMIT_ENV])
        logger.warning(f"Test limit {limit_kw} kW from {TEST_LIMIT_ENV}")
    staging = _staging(config)
    pump_file = DATA / ("pump_state_dry_run.json" if staging else "pump_state.json")
    garage_kw = gc.load_settings(config.garage_section).total_kw
    mode, guard = guard_settings(config.load_guard_section, garage_kw)
    available = lg.available_sheds(guard, garage_sheddable(now), pump_command(pump_file))
    verdict = lg.judge(samples, now, pc.factor(hour, settings), limit_kw, available)
    if verdict is None:
        logger.warning("No projection: no sample this hour, or the counter jumped")
        return 0
    done: list = []
    if verdict.over and not verdict.sheds:
        logger.warning(f"Over the limit at {verdict.counted_kw:.2f} kW with nothing to shed")
    if verdict.sheds:
        logger.warning(
            f"{mode}: shed {[s.load for s in verdict.sheds]}: counted {verdict.counted_kw:.2f} kW "
            f"over {limit_kw:.2f} kW, step {verdict.step_class}"
        )
        if mode == "act":
            done = _act(config, verdict, now, staging)
    try:
        influx = InfluxClient(config, timeout_ms=INFLUX_TIMEOUT_MS)
        point = verdict_point(verdict, now, sample, mode, done)
        influx.write_with_retry(bucket=config.influxdb_bucket_load_control, record=point)
    except Exception as e:
        logger.error(f"load_guard point not written: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
