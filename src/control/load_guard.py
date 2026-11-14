"""The 1-minute load guard: keep the hour's counted import under the cap.

Pure functions. The guard projects the hour from the 3EM energy counter
and the last minutes' power, names a new load step by its per-phase size,
and lists what to shed (garage, then the pump to ALE, then EVU).
"""

import datetime
from dataclasses import dataclass
from typing import Optional

RECENT_MIN = 5
# A shed needs the projection over the limit this many minutes in a row
CONFIRM_MIN = 5
STEP_AFTER_MIN = 3
STEP_BEFORE_MIN = 5
STEP_MIN_KW = 0.8
# 3 x 50 A at 230 V: a faster counter rise is a glitch, not a load
MAX_IMPORT_KW = 34.5
# A sample this close before the hour lets the hour's start be interpolated
INTERPOLATE_S = 600


@dataclass(frozen=True)
class Sample:
    """One 3EM reading: epoch seconds, per-phase power in kW, the net counter
    and the import-only counter in Wh. Only the import counter never falls."""

    t: float
    phase_kw: tuple[float, float, float]
    net_wh: float
    import_wh: float

    @property
    def total_kw(self) -> float:
        return sum(self.phase_kw)


@dataclass(frozen=True)
class Shed:
    load: str
    kw: float


@dataclass(frozen=True)
class GuardSettings:
    garage_kw: float
    pump_ale_kw: float
    pump_evu_kw: float


@dataclass(frozen=True)
class Verdict:
    hour_start: float
    projected_kw: float
    counted_kw: float
    limit_kw: float
    step_class: Optional[str]
    sheds: tuple[Shed, ...]
    # Over the limit and confirmed, also when nothing is left to shed
    over: bool = False


def hour_start(t: float) -> float:
    return t - t % 3600


def _energy_at_start(samples: list, start: float, first: Sample) -> float:
    """The net counter at the hour's start: interpolated from a sample just
    before it, else estimated back from the first sample's power."""
    before = [s for s in samples if start - INTERPOLATE_S <= s.t < start]
    if before and before[-1].import_wh <= first.import_wh:
        prev = before[-1]
        share = (start - prev.t) / (first.t - prev.t)
        return prev.net_wh + (first.net_wh - prev.net_wh) * share
    return first.net_wh - max(first.total_kw, 0.0) * (first.t - start) / 3.6


def projected_hour_kw(samples: list, now: float) -> Optional[float]:
    """The hour's mean import if the last RECENT_MIN minutes' power holds.

    None with no sample in the hour, an import counter that fell (a 3EM
    reboot) or one that rose faster than any real load (a glitch)."""
    start = hour_start(now)
    in_hour = [s for s in samples if start <= s.t <= now]
    if not in_hour:
        return None
    first, last = in_hour[0], in_hour[-1]
    if last.import_wh < first.import_wh - 1.0:
        return None
    span_h = max(last.t - first.t, 60.0) / 3600
    if (last.import_wh - first.import_wh) / 1000 / span_h > MAX_IMPORT_KW:
        return None
    recent = [s for s in in_hour if s.t > now - RECENT_MIN * 60] or [last]
    power_kw = max(sum(s.total_kw for s in recent) / len(recent), 0.0)
    so_far_kwh = max((last.net_wh - _energy_at_start(samples, start, first)) / 1000, 0.0)
    rest_kwh = power_kw * (start + 3600 - last.t) / 3600
    return so_far_kwh + rest_kwh


def _median(values: list) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def phase_step(samples: list, now: float) -> Optional[tuple[float, float, float]]:
    """Per-phase median of the last STEP_AFTER_MIN minutes minus the median
    of the STEP_BEFORE_MIN minutes before them, or None without data."""
    after = [s for s in samples if now - STEP_AFTER_MIN * 60 < s.t <= now]
    before = [
        s
        for s in samples
        if now - (STEP_AFTER_MIN + STEP_BEFORE_MIN) * 60 < s.t <= now - STEP_AFTER_MIN * 60
    ]
    if len(after) < STEP_AFTER_MIN or len(before) < STEP_BEFORE_MIN - 1:
        return None
    steps = []
    for p in range(3):
        steps.append(
            _median([s.phase_kw[p] for s in after]) - _median([s.phase_kw[p] for s in before])
        )
    return (steps[0], steps[1], steps[2])


def classify_step(step: tuple[float, float, float]) -> Optional[str]:
    """A label for a rising step; only the label depends on it, not the action."""
    up = [p for p in range(3) if step[p] >= STEP_MIN_KW * 0.6]
    if not up or max(step) < STEP_MIN_KW:
        return None
    if len(up) == 1:
        if up[0] in (0, 2) and 0.9 <= step[up[0]] <= 1.25:
            return "garage_heater"
        return f"single_phase_l{up[0] + 1}"
    if up == [0, 2] and all(0.9 <= step[p] <= 1.25 for p in up):
        return "garage_heater"
    if len(up) < 3:
        return "two_phase"
    mean = sum(step) / 3
    for name, low, high in (
        ("compressor", 0.8, 1.4),
        ("car_5kw", 1.6, 2.0),
        ("resistor_6kw", 2.0, 2.3),
        ("sauna", 2.3, 3.0),
        ("car_11kw", 3.2, 4.2),
    ):
        if low <= mean < high:
            return name
    return "balanced_other"


def sheds_needed(
    projected_kw: float,
    factor: float,
    limit_kw: float,
    now: float,
    available: list,
) -> tuple[Shed, ...]:
    """The fewest loads, in the given order, that bring the counted hour under
    the limit. Shedding saves its kW only for the rest of the hour."""
    rest_h = (hour_start(now) + 3600 - now) / 3600
    counted = projected_kw * factor
    chosen = []
    for shed in available:
        if counted <= limit_kw:
            break
        chosen.append(shed)
        counted -= shed.kw * rest_h * factor
    return tuple(chosen)


def judge(
    samples: list,
    now: float,
    factor: float,
    limit_kw: float,
    available: list,
) -> Optional[Verdict]:
    """This minute's verdict, or None without a projection."""
    projected = projected_hour_kw(samples, now)
    if projected is None:
        return None
    step = phase_step(samples, now)
    label = classify_step(step) if step is not None else None
    counted = projected * factor
    over = counted > limit_kw and _confirmed(samples, now, factor, limit_kw)
    sheds = sheds_needed(projected, factor, limit_kw, now, available) if over else ()
    return Verdict(hour_start(now), projected, counted, limit_kw, label, sheds, over)


def _confirmed(samples: list, now: float, factor: float, limit_kw: float) -> bool:
    """The projection was also over the limit in the minutes before, in this
    hour: a spike of a minute or two sheds nothing."""
    for back in range(1, CONFIRM_MIN):
        # Half a minute of slack: the runs do not start at the exact second
        t = now - back * 60 + 30
        if hour_start(t) != hour_start(now):
            return False
        earlier = projected_hour_kw([s for s in samples if s.t <= t], t)
        if earlier is None or earlier * factor <= limit_kw:
            return False
    return True


def available_sheds(settings: GuardSettings, garage_on: bool, pump_command: Optional[str]) -> list:
    """What can still be shed now, in shedding order."""
    sheds = []
    if garage_on:
        sheds.append(Shed("garage_off", settings.garage_kw))
    if pump_command == "ON":
        sheds.append(Shed("pump_ale", settings.pump_ale_kw))
    if pump_command in ("ON", "ALE"):
        sheds.append(Shed("pump_evu", settings.pump_evu_kw))
    return sheds


def utc(t: float) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc)
