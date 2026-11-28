"""Rule 1 and the manual paths of the hot water relay (pure code).

The relay is OFF unless a rule in act wants ON. A manual ON (the Shelly app
or web page: ON with no timer) is kept for a while, then set OFF. A manual
OFF while a rule had it ON suspends the rules. The CLI's circulate and pause
requests arrive here through the tick.
"""

from dataclasses import dataclass
from typing import Optional

from src.control.hardware_implementations import RelayState

# A timer this close to its end may have run out by itself: no manual OFF
TIMER_SLACK_S = 30
ACTIONS = ("circulate", "pause")


@dataclass
class Memory:
    last_cmd: Optional[str] = None
    last_on_at: Optional[int] = None
    manual_on_since: Optional[int] = None
    suspend_until: int = 0
    circulate_until: int = 0
    pause_until: int = 0


@dataclass(frozen=True)
class RelayLimits:
    on_timer_s: int
    manual_on_keep_s: int
    manual_off_suspend_s: int
    circulate_max_min: int


@dataclass(frozen=True)
class Action:
    # "on" renews the auto-off timer, "off" switches off, "none" leaves it
    command: str
    reason: str


def apply_request(mem: Memory, request: dict, now: int, limits: RelayLimits) -> str:
    """Apply a CLI request; returns what it did, for the log."""
    if "action" not in request or "minutes" not in request:
        raise ValueError(f"bad request {request}")
    action, minutes = request["action"], int(request["minutes"])
    if action not in ACTIONS or minutes < 0:
        raise ValueError(f"bad request {request}")
    if action == "circulate":
        minutes = min(minutes, limits.circulate_max_min)
        mem.circulate_until = now + minutes * 60
        mem.pause_until = 0
        mem.suspend_until = 0
        return f"circulate {minutes} min"
    mem.pause_until = now + minutes * 60
    mem.circulate_until = 0
    return f"pause {minutes} min"


def _saw_manual_off(mem: Memory, relay: RelayState, now: int, limits: RelayLimits) -> bool:
    if mem.last_cmd != "on" or relay.ison or mem.last_on_at is None:
        return False
    return now < mem.last_on_at + limits.on_timer_s - TIMER_SLACK_S


def relay_action(
    rule_on: bool,
    reason: str,
    relay: Optional[RelayState],
    mem: Memory,
    now: int,
    limits: RelayLimits,
    on_allowed: bool,
) -> Action:
    """What to do with the relay this tick. on_allowed is False when the
    clock is not synced or the tank data is stale."""
    if relay is None:
        return Action("none", "relay unreadable")
    if _saw_manual_off(mem, relay, now, limits):
        mem.suspend_until = now + limits.manual_off_suspend_s
        mem.circulate_until = 0
        mem.last_cmd = "off"
    if now < mem.pause_until:
        return Action("off" if relay.ison else "none", "paused")
    if relay.ison and not relay.has_timer:
        if mem.manual_on_since is None:
            mem.manual_on_since = now
        if now - mem.manual_on_since < limits.manual_on_keep_s:
            return Action("none", "manual ON kept")
        return Action("off", "manual ON time over")
    mem.manual_on_since = None
    want, why = rule_on, reason
    if now < mem.circulate_until:
        want, why = True, "circulate"
    elif now < mem.suspend_until:
        want, why = False, "suspended after a manual OFF"
    if want and not on_allowed:
        want, why = False, "no ON: clock not synced or tank data stale"
    if want:
        return Action("on", why)
    return Action("off" if relay.ison else "none", why)


def record(mem: Memory, action: Action, after: Optional[RelayState], now: int) -> None:
    """Remember the command that reached the relay."""
    if after is None or action.command == "none":
        return
    if action.command == "on" and after.ison:
        mem.last_cmd, mem.last_on_at = "on", now
    elif action.command == "off" and not after.ison:
        mem.last_cmd, mem.manual_on_since = "off", None
