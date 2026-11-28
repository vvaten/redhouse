"""The pre-pulse: a due EVU pulse in the minute before the pump's next ON.

It releases back to ALE, so the ON lands on its quarter edge with a fresh
release and an idle compressor, and skips its own pulse. If the pre-pulse
fails, the ON still pulses.
"""

from typing import Callable, Optional

from src.common.logger import setup_logger
from src.control.program_executor import HeatingProgramExecutor
from src.control.program_shape import PUMP_LOAD, program_is_well_formed
from src.control.pump_controller import PumpController

logger = setup_logger(__name__)

# The pre-pulse timer runs 30 s before each quarter edge
LEAD_S = 60


def next_pump_entry(programs: list, now: int) -> Optional[dict]:
    """The earliest unexecuted geothermal_pump entry after now across the
    given well-formed programs."""
    coming = [
        e
        for program in programs
        if program_is_well_formed(program) and PUMP_LOAD in program["loads"]
        for e in program["loads"][PUMP_LOAD]["schedule"]
        if e["timestamp"] > now and not e.get("executed_at")
    ]
    return min(coming, key=lambda e: e["timestamp"]) if coming else None


def skip_reason(
    pump: PumpController, programs: list, now: int, held: Callable[[int], bool]
) -> Optional[str]:
    """Why no pre-pulse runs now, or None when one should. held(t) tells
    whether a load guard hold runs at time t."""
    entry = next_pump_entry(programs, now)
    if entry is None or entry["command"] != "ON":
        return "the next pump entry is not ON"
    at = entry["timestamp"]
    if at - now > LEAD_S:
        return f"the next ON is {at - now} s away"
    if pump.last_command not in ("ALE", None):
        return f"the pump is {pump.last_command}, not ALE"
    due = HeatingProgramExecutor.due_pump_entry(programs, now)
    if due is not None and due["command"] == "EVU":
        return "the program holds the pump in EVU"
    if held(now) or held(at):
        return "a load guard hold blocks the ON"
    if not pump.pulse_due(at):
        return "the last release is fresh enough for the ON"
    return None


def run_pre_pulse(
    pump: PumpController, programs: list, now: int, held: Callable[[int], bool]
) -> str:
    """Pre-pulse when it is due. Returns "skipped", "pulsed" or "failed"."""
    reason = skip_reason(pump, programs, now, held)
    if reason is not None:
        logger.info(f"No pre-pulse: {reason}")
        return "skipped"
    logger.info("Pre-pulse before the coming ON")
    result = pump.pre_pulse(now)
    if not result["success"]:
        logger.error(f"Pre-pulse failed, the ON pulses instead: {result['error']}")
        return "failed"
    return "pulsed"
