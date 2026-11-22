"""Pump health: the pump is ON but heats with the heater only (pure core).

After the high-pressure alarm of 2026-11-16 the pump heated the house with
its heater, unseen. A full heater step (both stages, about 6.4 kW, balanced
over the phases) for most of the window, with no tank rise, is that state.
A heater step with the tank top climbing from cool is a boost, not an alarm.
"""

from dataclasses import dataclass
from typing import Optional

from src.control import hot_water_detect as hd

BASE_WINDOW_S = 3 * 3600
FULL_STEP_KW = 5.0
# The heater loads the phases evenly; the compressor loads L1 more than L2
BALANCE_KW = 0.15
FULL_SHARE = 0.4
# A boost climbs from a cool tank; the alarm of 2026-11-16 heated a 65 C one
BOOST_RISE_C = 2.0
BOOST_FROM_UNDER_C = 60.0


@dataclass(frozen=True)
class HealthSettings:
    no_compressor_min: int
    below_outdoor_c: float


def load_health_settings(section: dict) -> HealthSettings:
    missing = [k for k in ("no_compressor_min", "below_outdoor_c") if k not in section]
    if missing:
        raise ValueError(f"config.yaml pump_health section lacks {missing}")
    return HealthSettings(int(section["no_compressor_min"]), float(section["below_outdoor_c"]))


def quiet_base(series: hd.Series, now: int) -> Optional[tuple]:
    """Per-phase 10th percentile of the last 3 h: the house with the pump idle."""
    rows = [
        m.phases
        for t in range(now - BASE_WINDOW_S, now + 1, hd.MIN_S)
        if (m := series.get(t)) is not None and m.phases is not None
    ]
    if len(rows) < 60:
        return None
    return tuple(sorted(r[i] for r in rows)[len(rows) // 10] for i in range(3))


def heater_only(phases: tuple, base: tuple) -> bool:
    step = tuple(phases[i] - base[i] for i in range(3))
    return sum(step) >= FULL_STEP_KW and step[0] - step[1] <= BALANCE_KW


@dataclass(frozen=True)
class Verdict:
    alarm: bool
    reason: str
    heater_min: int
    top_rise_c: Optional[float]


def judge(
    minutes: list, now: int, on_since: Optional[int], outdoor_c: Optional[float], s: HealthSettings
) -> Verdict:
    """Alarm when the pump was ON the whole window, it is cold outside, the
    heater alone ran most of the window and the tank top did not rise."""
    window_s = s.no_compressor_min * 60
    if on_since is None or now - on_since < window_s:
        return Verdict(False, "pump not ON for the whole window", 0, None)
    if outdoor_c is None or outdoor_c >= s.below_outdoor_c:
        return Verdict(False, "not cold enough outside", 0, None)
    series = hd.Series(minutes)
    base = quiet_base(series, now)
    if base is None:
        return Verdict(False, "too little 3EM data", 0, None)
    return _window(series, now - window_s, now, base, s)


def _heater_minutes(series: hd.Series, start: int, stop: int, base: tuple) -> int:
    count = 0
    for t in range(start, stop + 1, hd.MIN_S):
        m = series.get(t)
        if m is not None and m.phases is not None and heater_only(m.phases, base):
            count += 1
    return count


def _window(series: hd.Series, start: int, now: int, base: tuple, s: HealthSettings) -> Verdict:
    heater = _heater_minutes(series, start, now, base)
    tops = series.values(start, now, "top")
    rise = max(tops) - tops[0] if tops else None
    if rise is not None and rise >= BOOST_RISE_C and tops[0] < BOOST_FROM_UNDER_C:
        return Verdict(False, "a boost: the tank top climbs from cool", heater, rise)
    if heater >= FULL_SHARE * s.no_compressor_min:
        return Verdict(True, "heater only, no tank rise", heater, rise)
    return Verdict(False, "the compressor runs", heater, rise)
