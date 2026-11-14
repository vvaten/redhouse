"""Time-limited load overrides written by the load guard.

The guard is the only writer. While an override runs, the executor applies
no program command to the pump, and the garage tick keeps the heat off
unless the garage is under its floor or its reading is stale.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from src.common.atomic_json import write_json
from src.control.program_shape import PUMP_LOAD

PUMP = PUMP_LOAD
GARAGE = "garage_heater"
COMMANDS = {PUMP: {"ALE", "EVU"}, GARAGE: {"OFF"}}
PUMP_STATES = {"ON", "ALE", "EVU"}
# No override may run longer than this, whatever the file says
MAX_OVERRIDE_S = 2 * 3600
# An ended override stays this long, so the hand-back cannot be missed
RESTORE_WINDOW_S = 2 * 3600


@dataclass(frozen=True)
class Override:
    load_id: str
    command: str
    since: float
    until: float
    reason: str
    # The pump command before the hold, for a hand-back with no program entry
    restore_to: Optional[str] = None

    def active(self, now: float) -> bool:
        return self.since <= now < self.until and self.until - self.since <= MAX_OVERRIDE_S

    def ended(self, now: float) -> bool:
        return self.until <= now < self.until + RESTORE_WINDOW_S


def _parse(entry) -> Optional[Override]:
    if not isinstance(entry, dict):
        return None
    try:
        restore_to = entry["restore_to"] if "restore_to" in entry else None
        override = Override(
            str(entry["load_id"]),
            str(entry["command"]),
            float(entry["since"]),
            float(entry["until"]),
            str(entry["reason"]),
            None if restore_to is None else str(restore_to),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if override.load_id not in COMMANDS or override.command not in COMMANDS[override.load_id]:
        return None
    if override.restore_to is not None and override.restore_to not in PUMP_STATES:
        return None
    return override


def read_overrides(path: Path) -> list:
    """Every well-formed override in the file; a broken file gives none."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    entries = data["overrides"] if isinstance(data, dict) and "overrides" in data else []
    parsed = [_parse(e) for e in entries] if isinstance(entries, list) else []
    return [o for o in parsed if o is not None]


def active_override(path: Path, load_id: str, now: float) -> Optional[Override]:
    """The running override of the load, the newest if several run."""
    running = [o for o in read_overrides(path) if o.load_id == load_id and o.active(now)]
    return max(running, key=lambda o: o.since) if running else None


def ended_override(path: Path, load_id: str, now: float) -> Optional[Override]:
    """The newest override of the load that ended within RESTORE_WINDOW_S."""
    ended = [o for o in read_overrides(path) if o.load_id == load_id and o.ended(now)]
    return max(ended, key=lambda o: o.until) if ended else None


def write_overrides(path: Path, overrides: list, now: float) -> None:
    """Keep overrides that run or ended within RESTORE_WINDOW_S; write atomically."""
    kept = [o for o in overrides if o.until + RESTORE_WINDOW_S > now]
    write_json(path, {"overrides": [asdict(o) for o in kept]})
