"""Hot water tick: one decision a minute for the circulation relay.

Phase A (cycle log, boost timer, alerts) always runs. The relay rules run by
their modes. The tick never commands the pump and takes no control lock.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from src.common.atomic_json import write_json
from src.common.logger import setup_logger
from src.control import hot_water_alerts as ha
from src.control import hot_water_boost as hb
from src.control import hot_water_cycles as hc
from src.control import hot_water_detect as hd
from src.control import hot_water_io as hio
from src.control import hot_water_manual as hm
from src.control import hot_water_rules as hr
from src.control.hardware_implementations import RelayState

logger = setup_logger(__name__)

WINDOW_S = 6 * 3600
HISTORY_S = 16 * 86400
# Data dated further ahead than this is wrong, so it counts as stale
FUTURE_S = 120
POINT_EVERY_S = 15 * 60
STATE_FILE = "hot_water_state.json"
REQUEST_FILE = "hot_water_request.json"
MODE_KEYS = ("relay_default",) + hr.RULES
STAGING_MODES_ENV = "HOT_WATER_STAGING_MODES"
REQUIRED = (
    "relay_url",
    "relay_on_timer_s",
    "manual_on_keep_s",
    "manual_off_suspend_s",
    "circulate_max_min",
    "tank_top_field",
    "tank_bottom_field",
    "data_stale_s",
    "pump_start_setting_c",
    "start_offset_c",
    "hot_water_time_min",
    "floor_c",
    "modes",
    "boost",
    "flush",
    "preheat",
    "pre_on",
    "comfort",
    "alerts",
    "boost_budget",
    "boost_kw",
    "planner_link",
)


def modes_from(section: dict, staging: bool) -> dict:
    """The modes of config.yaml; in staging the env value may replace them."""
    modes = dict(section["modes"])
    if staging and STAGING_MODES_ENV in os.environ:
        modes.update(json.loads(os.environ[STAGING_MODES_ENV]))
        logger.warning(f"Staging modes from {STAGING_MODES_ENV}: {modes}")
    missing = [k for k in MODE_KEYS if k not in modes]
    if missing:
        raise ValueError(f"hot_water.modes lacks {missing}")
    bad = {k: v for k, v in modes.items() if k not in MODE_KEYS or v not in hr.MODES}
    if bad or modes["relay_default"] == "off":
        raise ValueError(f"hot_water.modes has bad values {bad or modes}")
    return modes


def load_settings(section: dict, staging: bool, anchor_utc: Optional[int] = None) -> dict:
    missing = [k for k in REQUIRED if k not in section]
    if missing:
        raise ValueError(f"config.yaml hot_water section lacks {missing}")
    settings = dict(section)
    settings["modes"] = modes_from(section, staging)
    settings["boost"] = hb.load_boost_settings(section["boost"], anchor_utc)
    settings["limits"] = hm.RelayLimits(
        on_timer_s=int(section["relay_on_timer_s"]),
        manual_on_keep_s=int(section["manual_on_keep_s"]),
        manual_off_suspend_s=int(section["manual_off_suspend_s"]),
        circulate_max_min=int(section["circulate_max_min"]),
    )
    if not 120 < settings["limits"].on_timer_s <= 600:
        raise ValueError("hot_water relay_on_timer_s must cover a skipped minute, at most 600")
    return settings


@dataclass
class TickIO:
    """The outside world of one tick, replaceable in tests."""

    read_minutes: Callable
    read_history: Callable
    read_prices: Callable
    read_program: Callable
    write: Callable
    mail: Callable
    clock_ok: Callable
    boot_time: Callable


@dataclass
class TickState:
    last_tick: int = 0
    last_point_at: int = 0
    last_point_sig: str = ""
    applied_request_at: int = 0
    mem: hm.Memory = field(default_factory=hm.Memory)
    rules: hr.RuleState = field(default_factory=hr.RuleState)
    sent: dict = field(default_factory=dict)
    # [begin, end, rule] of the relay's ON spans in the last WINDOW_S
    sent_spans: list = field(default_factory=list)
    # The boot whose restart mail is not sent yet, 0 when none
    restart_boot: float = 0.0
    # The time of the newest tank reading seen
    last_tank_t: int = 0


def load_state(path: Path) -> Optional[TickState]:
    """The RAM state, or None when there is none (first tick after a boot)."""
    try:
        data = json.loads(path.read_text())
        rules = data["rules"]
        rules["since"] = {k: int(v) for k, v in rules["since"].items()}
        return TickState(
            last_tick=int(data["last_tick"]),
            last_point_at=int(data["last_point_at"]),
            last_point_sig=str(data["last_point_sig"]),
            applied_request_at=int(data["applied_request_at"]),
            mem=hm.Memory(**data["mem"]),
            rules=hr.RuleState(**rules),
            sent={k: float(v) for k, v in data["sent"].items()},
            sent_spans=[[int(a), int(b), str(r)] for a, b, r in data["sent_spans"]],
            restart_boot=float(data["restart_boot"]),
            last_tank_t=int(data["last_tank_t"]),
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as e:
        logger.error(f"Hot water state unreadable, starting afresh: {e}")
        return TickState()


def save_state(path: Path, state: TickState) -> None:
    write_json(path, asdict(state))


@dataclass(frozen=True)
class Tank:
    top: Optional[float]
    bottom: Optional[float]
    age_s: Optional[float]
    fresh: bool


def latest_tank(minutes: list, now: int, stale_s: int) -> Tank:
    """The newest tank reading; data from the future makes it stale."""
    future = [m for m in minutes if m.t > now + FUTURE_S]
    readings = [m for m in minutes if m.top is not None and m.bottom is not None]
    if not readings:
        return Tank(None, None, None, False)
    last = max(readings, key=lambda m: m.t)
    age = now - last.t
    fresh = not future and -FUTURE_S <= age <= stale_s
    return Tank(last.top, last.bottom, age, fresh)


def take_request(run_dir: Path, mem: hm.Memory, now: int, limits: hm.RelayLimits) -> str:
    """Apply the CLI request file; returns what was done. A good request's
    file stays until the state is saved, so a killed tick applies it again."""
    path = run_dir / REQUEST_FILE
    try:
        request = json.loads(path.read_text())
    except FileNotFoundError:
        return ""
    except (OSError, ValueError) as e:
        logger.error(f"Hot water request unreadable, removed: {e}")
        path.unlink()
        return ""
    try:
        done = hm.apply_request(mem, request, now, limits)
    except ValueError as e:
        logger.error(f"Hot water request refused: {e}")
        path.unlink()
        return ""
    logger.warning(f"Hot water request applied at {now}: {done}")
    return done


@dataclass(frozen=True)
class Inputs:
    tank: Tank
    view: hc.CycleView
    prices: dict
    entries: list
    clock_ok: bool
    history_ok: bool
    # top_max(start, stop): the tank top's maximum in the minute data
    top_max: Callable


def _top_max(minutes: list) -> Callable:
    def top_max(start: int, stop: int) -> Optional[float]:
        tops = [m.top for m in minutes if start <= m.t <= stop and m.top is not None]
        return max(tops) if tops else None

    return top_max


def read_inputs(now: int, settings: dict, io: TickIO) -> Inputs:
    minutes = io.read_minutes(now - WINDOW_S, now + 3600)
    tank = latest_tank(minutes, now, int(settings["data_stale_s"]))
    prices = io.read_prices(now - 2 * 3600, now + 36 * 3600)
    rows = io.read_history(now - HISTORY_S)
    past = [m for m in minutes if m.t <= now]

    def price(t: int) -> Optional[float]:
        h = t - t % 3600
        return prices[h] if h in prices else None

    cycles = hd.find_cycles(past, now, price)
    history_ok = rows is not None
    view = hc.build_view(rows if history_ok else [], cycles, settings["boost"])
    entries = io.read_program(now)
    return Inputs(tank, view, prices, entries, bool(io.clock_ok()), history_ok, _top_max(past))


def start_level(settings: dict, view: hc.CycleView) -> float:
    if view.start_level is not None:
        return view.start_level
    return float(settings["pump_start_setting_c"]) + float(settings["start_offset_c"])


def context(now: int, settings: dict, inp: Inputs) -> hr.Context:
    boost = hb.status(inp.view.records, now, settings["boost"])
    return hr.Context(
        now=now,
        top=inp.tank.top,
        bottom=inp.tank.bottom,
        start_level=start_level(settings, inp.view),
        boost=boost,
        open_cycle=inp.view.open,
        last_cycle_start=inp.view.last_start,
        prices=inp.prices,
        program=hio.hour_program(inp.entries, now - 3600, 40),
        settings=settings,
    )


def _switch(relay, action: hm.Action, timer_s: int) -> Optional[RelayState]:
    if action.command == "on":
        return relay.on(timer_s)
    if action.command == "off":
        return relay.off()
    return None


def _spans(state: TickState, now: int, rule: str, on: bool) -> list:
    """Relay ON spans of the last WINDOW_S as [begin, end, rule]."""
    spans = [s for s in state.sent_spans if s[1] >= now - WINDOW_S]
    if on and spans and spans[-1][2] == rule and spans[-1][1] >= now - 180:
        spans[-1][1] = now
    elif on:
        spans.append([now, now, rule])
    return spans


@dataclass(frozen=True)
class TickResult:
    action: hm.Action
    decision: hr.Decision
    before: Optional[RelayState]
    after: Optional[RelayState]
    inputs: Inputs
    context: hr.Context
    alerts: list
    request: str
    first_tick: bool


def _note_boot(state: TickState, first_tick: bool, now: int, boot: float) -> None:
    """A first tick soon after the boot owes the restart mail until it is sent."""
    if first_tick and 0 < now - boot < ha.RESTART_S:
        state.restart_boot = boot


def _note_tank(state: TickState, tank: Tank, now: int) -> None:
    if tank.age_s is not None:
        state.last_tank_t = int(now - tank.age_s)
    elif state.last_tank_t == 0:
        state.last_tank_t = now


def _report(now: int, settings: dict, inp: Inputs, state: TickState, io: TickIO, points) -> list:
    """Write the points and mail the alerts; nothing before the clock syncs,
    as the points and the alert windows would carry a wrong time."""
    if not inp.clock_ok:
        return []
    io.write(points)
    alerts = ha.alerts(now, settings, inp, state)
    ha.send(alerts, state.sent, now, io.mail)
    if f"restart:{int(state.restart_boot)}" in state.sent:
        state.restart_boot = 0.0
    return alerts


def run_once(now: int, settings: dict, relay, io: TickIO, run_dir: Path) -> TickResult:
    """One tick with its IO; the RAM state lives in run_dir."""
    path = run_dir / STATE_FILE
    loaded = load_state(path)
    state = loaded if loaded is not None else TickState()
    _note_boot(state, loaded is None, now, float(io.boot_time()))
    request = take_request(run_dir, state.mem, now, settings["limits"])
    if request:
        state.applied_request_at = now
    inp = read_inputs(now, settings, io)
    _note_tank(state, inp.tank, now)
    ctx = context(now, settings, inp)
    rule_modes = {k: settings["modes"][k] for k in hr.RULES}
    decision = hr.decide(ctx, state.rules, rule_modes)
    before = relay.state()
    on_allowed = inp.clock_ok and inp.tank.fresh and inp.history_ok
    action = hm.relay_action(
        decision.on, decision.reason, before, state.mem, now, settings["limits"], on_allowed
    )
    if settings["modes"]["relay_default"] != "act":
        action = hm.Action("none", f"relay_default dry_run: would {action.command}")
    after = _switch(relay, action, settings["limits"].on_timer_s)
    hm.record(state.mem, action, after, now)
    rule = decision.rule if action.command == "on" and decision.on else action.reason
    state.sent_spans = _spans(state, now, rule, action.command == "on")
    points = []
    if inp.history_ok:
        cap = int(settings["hot_water_time_min"])
        is_on = _is_on(inp.entries)
        points += hc.cycle_points(inp.view, is_on, cap, state.sent_spans)
    points += _tick_points(now, settings, state, ctx, decision, action, before, after)
    alerts = _report(now, settings, inp, state, io, points)
    state.last_tick = now
    save_state(path, state)
    if request:
        (run_dir / REQUEST_FILE).unlink()
    return TickResult(action, decision, before, after, inp, ctx, alerts, request, loaded is None)


def _is_on(entries: list) -> Callable:
    def is_on(t: int) -> bool:
        return hio.command_at(entries, t) == "ON"

    return is_on


def _tick_fields(settings, ctx, decision, action, shown) -> dict:
    fields = {
        "relay_on": -1 if shown is None else int(shown.ison),
        "relay_timer": -1 if shown is None else int(shown.has_timer),
        "command": action.command,
        "rule": decision.rule,
        "reason": action.reason,
        "would": ",".join(f"{k}:{v}" for k, v in decision.would.items()),
        "start_level": float(ctx.start_level),
        "pending": bool(ctx.boost.pending),
        "next_expiry": hb.format_utc(ctx.boost.next_expiry),
        "modes": ",".join(f"{k}={v}" for k, v in settings["modes"].items()),
    }
    if ctx.top is not None and ctx.bottom is not None:
        fields["top"], fields["bottom"] = float(ctx.top), float(ctx.bottom)
    return fields


def _relay_points(now: int, action: hm.Action, before, after) -> list:
    """A load_control point when the relay switched, or when a switch failed."""
    was_on = before is not None and before.ison
    if after is not None and after.ison != was_on:
        return [hio.relay_point(now, after.ison, action.reason, True)]
    if action.command in ("on", "off") and after is None:
        return [hio.relay_point(now, action.command == "on", action.reason, False)]
    return []


def _tick_points(now, settings, state, ctx, decision, action, before, after) -> list:
    """The hot_water point on a change and every 15 min; a relay point on a switch."""
    shown = after if after is not None else before
    fields = _tick_fields(settings, ctx, decision, action, shown)
    sig = json.dumps({k: v for k, v in fields.items() if k not in ("top", "bottom")})
    points = []
    if sig != state.last_point_sig or now - state.last_point_at >= POINT_EVERY_S:
        points.append(hio.tick_point(now, fields))
        state.last_point_sig, state.last_point_at = sig, now
    return points + _relay_points(now, action, before, after)
