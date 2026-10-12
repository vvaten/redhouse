#!/usr/bin/env python
"""The structure every consumer of a heating program relies on."""

PUMP_LOAD = "geothermal_pump"
PUMP_COMMANDS = {"ON", "ALE", "EVU"}


def _entry_is_well_formed(load_id: str, entry: object) -> bool:
    if not isinstance(entry, dict) or "timestamp" not in entry or "command" not in entry:
        return False
    if "local_time" not in entry or not isinstance(entry["local_time"], str):
        return False
    timestamp = entry["timestamp"]
    if not isinstance(timestamp, int) or isinstance(timestamp, bool):
        return False
    return load_id != PUMP_LOAD or entry["command"] in PUMP_COMMANDS


def program_is_well_formed(program: object) -> bool:
    """A dict with version and loads; each load a dict with a list schedule of
    entries with an int timestamp, a local_time and a command, a valid one for
    the pump."""
    if not isinstance(program, dict) or "version" not in program or "loads" not in program:
        return False
    if not isinstance(program["loads"], dict):
        return False
    for load_id, load in program["loads"].items():
        if not isinstance(load, dict) or "schedule" not in load:
            return False
        if not isinstance(load["schedule"], list):
            return False
        if not all(_entry_is_well_formed(load_id, e) for e in load["schedule"]):
            return False
    return True
