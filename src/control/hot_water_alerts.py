"""Alerts of the hot water tick: each kind at most once per 6 h, and each
event (one boost, one restart) once."""

import datetime
from dataclasses import dataclass

from src.common.local_time import HELSINKI
from src.control import hot_water_boost as hb

REPEAT_S = 6 * 3600
# A drift lasts for days; once a day is enough
REPEAT_BY_KIND = {"start_drift": 86400}
# An event older than this is not mailed, so a reboot does not repeat it
FRESH_S = 3600
NO_DATA_S = 15 * 60
# A first tick this soon after the boot means the Pi restarted
RESTART_S = 3600
# The restart mail is retried this long, as the network may come back late
RESTART_KEEP_S = 24 * 3600
SUBJECT = "[RedHouse WARNING] Hot water: "


@dataclass(frozen=True)
class Alert:
    key: str
    kind: str
    subject: str
    body: str


def local(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(epoch, HELSINKI).strftime("%a %Y-%m-%d %H:%M")


def _tank(now: int, settings: dict, tank, last_tank_t: int) -> list:
    out = []
    if now - last_tank_t > NO_DATA_S:
        body = f"No tank data since {local(last_tank_t)}."
        out.append(Alert("tank_data", "tank_data", "no tank data", body))
    floor = float(settings["floor_c"])
    if tank.top is not None and tank.fresh and tank.top < floor:
        body = f"Tank top {tank.top:.1f} C at {local(now)}, under the {floor:.0f} C floor."
        out.append(Alert("tank_floor", "tank_floor", "tank under the floor", body))
    return out


def _drift(settings: dict, view) -> list:
    expected = float(settings["pump_start_setting_c"]) + float(settings["start_offset_c"])
    limit = float(settings["alerts"]["start_drift_c"])
    if view.start_level is None or view.start_level_count < 5:
        return []
    if abs(view.start_level - expected) <= limit:
        return []
    body = (
        f"The pump starts its runs at a bottom of {view.start_level:.1f} C, expected "
        f"{expected:.1f} C. Check the hot water start setting on the pump display."
    )
    return [Alert("start_drift", "start_drift", "start level drift", body)]


def _boost_events(now: int, settings: dict, records: list, top_max) -> list:
    s = settings["boost"]
    out = []
    for r in records:
        if r.kind == hb.BOOST and r.unexpected and now - r.start < FRESH_S:
            body = (
                f"A boost started at {local(r.start)} with no expiry due. A restart moved "
                "the timer: enter the new anchor with hotwater anchor."
            )
            out.append(Alert(f"unexpected:{r.start}", "unexpected_boost", "unexpected boost", body))
    for r in hb.incomplete(records, now, top_max):
        if now - (r.start + hb.INCOMPLETE_AFTER_S) < FRESH_S:
            body = (
                f"The boost from {local(r.start)} is under 62 C after 3 h. The boost stalled "
                "(the heater did not run), or the anchor is wrong and this was a regular "
                "run; check the pump display, and check for an unexpected-boost mail."
            )
            out.append(Alert(f"incomplete:{r.start}", "incomplete_boost", "boost incomplete", body))
    acts = settings["modes"]["boost_trigger"] == "act"
    e = hb.missed(records, now, s, acts)
    if e is not None:
        moment = hb.slot_end(e, s) if acts else hb.window_end(e, s)
        if now - moment < FRESH_S:
            body = f"No boost came for the expiry of {local(e)}. Check the pump."
            out.append(Alert(f"missed:{e}", "missed_boost", "boost missed", body))
    return out


def alerts(now: int, settings: dict, inp, state) -> list:
    """Every alert due this tick. Without the cycle history the boost alerts
    wait: the window's cycles alone would be labelled wrongly."""
    out = _tank(now, settings, inp.tank, state.last_tank_t) + _drift(settings, inp.view)
    if inp.history_ok:
        out += _boost_events(now, settings, inp.view.records, inp.top_max)
    boot = state.restart_boot
    if boot and now - boot < RESTART_KEEP_S:
        body = (
            f"The Pi restarted at {local(boot)}. If the house lost power, the pump "
            "restarted too: confirm the boost anchor with hotwater anchor."
        )
        out.append(Alert(f"restart:{int(boot)}", "pi_restart", "Pi restart", body))
    return out


def send(alerts: list, sent: dict, now: int, mail) -> None:
    """Mail each new alert, at most one per kind per its repeat time."""
    for a in alerts:
        kind_key = f"kind:{a.kind}"
        if a.key in sent and a.key != a.kind:
            continue
        repeat = REPEAT_BY_KIND[a.kind] if a.kind in REPEAT_BY_KIND else REPEAT_S
        if kind_key in sent and now - sent[kind_key] < repeat:
            continue
        if mail(SUBJECT + a.subject, a.body):
            sent[a.key] = now
            sent[kind_key] = now
    for key in [k for k, t in sent.items() if now - t > 30 * 86400]:
        del sent[key]
