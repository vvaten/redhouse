"""Pump health: the pump is ON but runs nothing (pure core).

On 2026-11-16 a high-pressure alarm stopped the pump for 144 min of ON
hours, unseen. Normal ON idles last up to 16 min at night, 77 min by day.

L2 carries the compressor and the heater but not the garage heaters. The
3EM also sees the battery and the solar panels behind it, both balanced
over the phases, so a third of their net power comes off L2.
"""

from dataclasses import dataclass
from typing import Optional

from src.control import hot_water_detect as hd

BASE_WINDOW_S = 24 * 3600
# The quiet base: this share from the bottom of the last 24 h of L2
BASE_SHARE = 0.05
BASE_MIN_ROWS = 6 * 60
STEP_KW = 0.5
# A run this short inside an idle stretch is a blip, not the pump
BLIP_MIN = 2
# CheckWatt data arrives up to about 8 min late; those minutes are skipped
MIN_DATA_SHARE = 0.85
# The 3EM sees a CheckWatt step 1 to 2 min after its time stamp
BEHIND_LAG_MIN = 2
# CheckWatt fields (W) and their sign in the power behind the 3EM
BEHIND_SIGN = {"BatteryCharge": 1.0, "BatteryDischarge": -1.0, "SolarYield": -1.0}


@dataclass(frozen=True)
class HealthSettings:
    no_step_min: int
    below_outdoor_c: float


def load_health_settings(section: dict) -> HealthSettings:
    missing = [k for k in ("no_step_min", "below_outdoor_c") if k not in section]
    if missing:
        raise ValueError(f"config.yaml pump_health section lacks {missing}")
    return HealthSettings(int(section["no_step_min"]), float(section["below_outdoor_c"]))


@dataclass(frozen=True)
class Verdict:
    alarm: bool
    reason: str
    idle_min: int
    base_kw: Optional[float]


def _behind(behind: dict, t: int) -> Optional[float]:
    found = [
        behind[u] for u in range(t - BEHIND_LAG_MIN * hd.MIN_S, t + 1, hd.MIN_S) if u in behind
    ]
    return sum(found) / len(found) if found else None


def _l2(series: hd.Series, behind: dict, t: int) -> Optional[float]:
    """L2 without the battery and the solar panels; None without both data."""
    m = series.get(t)
    net = _behind(behind, t)
    if m is None or m.phases is None or net is None:
        return None
    return m.phases[1] - net / 3


def quiet_base(series: hd.Series, behind: dict, now: int) -> Optional[float]:
    """L2 with the pump idle: a low percentile of the last 24 h."""
    rows = sorted(
        v
        for t in range(now - BASE_WINDOW_S, now + 1, hd.MIN_S)
        if (v := _l2(series, behind, t)) is not None
    )
    if len(rows) < BASE_MIN_ROWS:
        return None
    return rows[int(len(rows) * BASE_SHARE)]


def judge(
    minutes: list,
    behind: dict,
    now: int,
    on_since: Optional[int],
    outdoor_c: Optional[float],
    s: HealthSettings,
) -> Verdict:
    """Alarm when the pump was ON the whole window, it is cold outside and
    L2 never rose above its quiet base for longer than a blip. behind is
    {minute start: kW}, battery charge minus discharge minus solar yield."""
    now -= now % hd.MIN_S
    window_s = s.no_step_min * 60
    if on_since is None or now - on_since < window_s:
        return Verdict(False, "pump not ON for the whole window", 0, None)
    if outdoor_c is None:
        return Verdict(False, "no outdoor reading", 0, None)
    if outdoor_c >= s.below_outdoor_c:
        return Verdict(False, "not cold enough outside", 0, None)
    series = hd.Series(minutes)
    base = quiet_base(series, behind, now)
    if base is None:
        return Verdict(False, "too little data for the base", 0, None)
    return _window(series, behind, now - window_s + hd.MIN_S, now, base)


def _window(series: hd.Series, behind: dict, start: int, now: int, base: float) -> Verdict:
    seen = idle = run = 0
    for t in range(start, now + 1, hd.MIN_S):
        v = _l2(series, behind, t)
        if v is None:
            continue
        seen += 1
        if v - base >= STEP_KW:
            run += 1
            if run > BLIP_MIN:
                return Verdict(False, "the pump runs", idle, base)
        else:
            run = 0
            idle += 1
    total = (now - start) // hd.MIN_S + 1
    if seen < MIN_DATA_SHARE * total:
        return Verdict(False, "too little data in the window", idle, base)
    return Verdict(True, "ON and nothing runs", idle, base)
