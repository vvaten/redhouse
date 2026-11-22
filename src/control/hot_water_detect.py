"""Hot water cycles from the tank sensors and the 3EM phases (pure code).

A cycle is a run of minutes with the tank bottom rising, or a heater step on
a hot tank. The kind (regular run, legionella boost, heater run) needs the
boost timer and is set in hot_water_boost.
"""

import statistics
from dataclasses import dataclass
from typing import Callable, Optional

MIN_S = 60
HEAT_RATE_C_PER_MIN = 0.08
START_LEAD_MIN = 3
MERGE_GAP_MIN = 10
MIN_GAIN_C = 1.0
# A cycle must show a compressor or heater step for this long
MIN_STEP_KW = 1.5
MIN_STEP_MIN = 3
BASE_FROM_MIN, BASE_TO_MIN = 40, 5
# One heater stage is about 3 kW; both are 6.4 kW
HEATER_STEP_KW = 2.5
DRAW_C = 0.5
DRAW_SPAN_MIN = 10
DRAW_BEFORE_MIN, DRAW_AFTER_MIN = 25, 5
HOT_TOP_C = 55.0
# A cycle is closed once its bottom has not risen for this long
CLOSED_AFTER_MIN = 15
SLOW_FALL_C_PER_H = 0.8
DIP_C = 0.3
DIP_SPAN_MIN = 5
FILL_MAX_MIN = 3


@dataclass(frozen=True)
class Minute:
    t: int
    top: Optional[float]
    bottom: Optional[float]
    phases: Optional[tuple]


@dataclass(frozen=True)
class Cycle:
    start: int
    end: int
    open: bool
    start_top: Optional[float]
    start_bottom: Optional[float]
    peak_top: Optional[float]
    kwh: float
    eur: float
    step_min: int
    # A heater step on a hot tank during a draw, for MIN_STEP_MIN or more
    heater: bool
    slow_start: bool
    source: str

    @property
    def minutes(self) -> int:
        return (self.end - self.start) // MIN_S + 1


def _fill_field(values: list) -> list:
    """Fill runs of up to FILL_MAX_MIN missing values by linear interpolation."""
    out = list(values)
    known = [i for i, v in enumerate(values) if v is not None]
    for left, right in zip(known, known[1:]):
        gap = right - left
        if 1 < gap <= FILL_MAX_MIN + 1:
            a, b = values[left], values[right]
            for k in range(1, gap):
                out[left + k] = a + (b - a) * k / gap
    return out


def _grid(minutes: list) -> dict:
    """{minute start: Minute} on a full grid, short gaps filled per field."""
    by_t = {m.t - m.t % MIN_S: m for m in minutes}
    if not by_t:
        return {}
    times = list(range(min(by_t), max(by_t) + MIN_S, MIN_S))
    rows = [by_t[t] if t in by_t else None for t in times]
    top = _fill_field([None if r is None else r.top for r in rows])
    bottom = _fill_field([None if r is None else r.bottom for r in rows])
    phase = [_fill_field([_phase(r, i) for r in rows]) for i in range(3)]
    grid = {}
    for k, t in enumerate(times):
        phases = _joined(phase[0][k], phase[1][k], phase[2][k])
        if top[k] is not None or bottom[k] is not None or phases is not None:
            grid[t] = Minute(t, top[k], bottom[k], phases)
    return grid


def _phase(row: Optional[Minute], i: int) -> Optional[float]:
    return None if row is None or row.phases is None else row.phases[i]


def _joined(*values: Optional[float]) -> Optional[tuple]:
    return None if any(v is None for v in values) else tuple(values)


class Series:
    """Minute data on a regular grid; short gaps are filled, longer ones are None."""

    def __init__(self, minutes: list):
        self.by_t = _grid(minutes)
        times = sorted(self.by_t)
        self.first = times[0] if times else 0
        self.last = times[-1] if times else -MIN_S

    def get(self, t: int) -> Optional[Minute]:
        return self.by_t[t] if t in self.by_t else None

    def times(self) -> range:
        return range(self.first, self.last + MIN_S, MIN_S)

    def value(self, t: int, name: str) -> Optional[float]:
        m = self.get(t)
        return None if m is None else getattr(m, name)

    def values(self, start: int, stop: int, name: str) -> list:
        found = [self.value(t, name) for t in range(start, stop + MIN_S, MIN_S)]
        return [v for v in found if v is not None]


def _slope(series: Series, t: int) -> Optional[float]:
    """The 3-min mean of the bottom's rise per minute, centred on t."""
    after = series.value(t + MIN_S, "bottom")
    before = series.value(t - 2 * MIN_S, "bottom")
    if after is None or before is None:
        return None
    return (after - before) / 3


def base_kw(series: Series, start: int) -> Optional[tuple]:
    """Per-phase 20th percentile in the 40 to 5 min before start."""
    rows = [
        m.phases
        for t in range(start - BASE_FROM_MIN * MIN_S, start - BASE_TO_MIN * MIN_S + 1, MIN_S)
        if (m := series.get(t)) is not None and m.phases is not None
    ]
    if len(rows) < 10:
        return None
    return tuple(_quantile([r[i] for r in rows], 0.2) for i in range(3))


def _quantile(values: list, q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))]


def _spans(flags: list, gap: int) -> list:
    """(first, last) index runs of True, merging gaps of at most gap."""
    spans: list = []
    for i, flag in enumerate(flags):
        if not flag:
            continue
        if spans and i - spans[-1][1] - 1 <= gap:
            spans[-1][1] = i
        else:
            spans.append([i, i])
    return [tuple(span) for span in spans]


def _steps(series: Series, start: int, end: int, base: Optional[tuple]) -> list:
    """(t, total step kW, per-phase steps) for each minute with phases."""
    if base is None:
        return []
    out = []
    for t in range(start, end + MIN_S, MIN_S):
        m = series.get(t)
        if m is not None and m.phases is not None:
            per = tuple(m.phases[i] - base[i] for i in range(3))
            out.append((t, sum(per), per))
    return out


def _slow_start(series: Series, start: int) -> bool:
    """Bottom falling under 0.8 C/h in the hour before, with no dip."""
    hour = series.values(start - 60 * MIN_S, start - MIN_S, "bottom")
    if len(hour) < 45:
        return False
    fall = hour[0] - hour[-1]
    if not 0 <= fall < SLOW_FALL_C_PER_H:
        return False
    span = DIP_SPAN_MIN
    return all(hour[i] - hour[i + span] <= DIP_C for i in range(len(hour) - span))


def _build(
    series: Series, start: int, end: int, open_: bool, source: str, price: Callable
) -> Optional[Cycle]:
    base = base_kw(series, start)
    steps = [s for s in _steps(series, start - 2 * MIN_S, end, base) if s[1] >= MIN_STEP_KW]
    if len(steps) < MIN_STEP_MIN and not open_:
        return None
    tops = series.values(start, end, "top")
    hot = [t for t, _, _ in steps if _heater_minute(series, t)]
    eur = 0.0
    for t, kw, _ in steps:
        p = price(t)
        eur += 0.0 if p is None else kw / 60 * p
    return Cycle(
        start=start,
        end=end,
        open=open_,
        start_top=series.value(start, "top"),
        start_bottom=series.value(start - MIN_S, "bottom"),
        peak_top=max(tops) if tops else None,
        kwh=sum(kw for _, kw, _ in steps) / 60,
        eur=eur,
        step_min=len(steps),
        heater=len(hot) >= MIN_STEP_MIN,
        slow_start=_slow_start(series, start),
        source=source,
    )


def _hot(series: Series, t: int) -> bool:
    top = series.value(t, "top")
    return top is not None and top > HOT_TOP_C


def _bottom_cycles(series: Series, now: int, price: Callable) -> list:
    times = list(series.times())
    flags = [(s := _slope(series, t)) is not None and s > HEAT_RATE_C_PER_MIN for t in times]
    cycles = []
    for a, b in _spans(flags, MERGE_GAP_MIN):
        start = times[a] - START_LEAD_MIN * MIN_S
        end = times[b]
        first = series.value(start, "bottom")
        peak = max(series.values(start, end + MIN_S, "bottom"), default=None)
        if first is None or peak is None or peak - first < MIN_GAIN_C:
            continue
        open_ = now - end < CLOSED_AFTER_MIN * MIN_S
        cycle = _build(series, start, end, open_, "bottom", price)
        if cycle is not None:
            cycles.append(cycle)
    return cycles


def _draw_near(series: Series, t: int) -> bool:
    """A fast draw (the bottom down DRAW_C in 10 min) from 25 min before t
    to 5 min after: the pump answers it with the heater on a hot tank."""
    values = [
        series.value(u, "bottom")
        for u in range(t - DRAW_BEFORE_MIN * MIN_S, t + DRAW_AFTER_MIN * MIN_S + 1, MIN_S)
    ]
    span = DRAW_SPAN_MIN
    for i in range(len(values) - span):
        a, b = values[i], values[i + span]
        if a is not None and b is not None and a - b >= DRAW_C:
            return True
    return False


def _heater_minute(series: Series, t: int) -> bool:
    """A heater step on a hot tank during a draw. A sauna or a car makes the
    same step, but not with a draw on a tank over 55 C."""
    m = series.get(t)
    if m is None or m.phases is None or not _hot(series, t):
        return False
    base = base_kw(series, t)
    if base is None or sum(m.phases) - sum(base) < HEATER_STEP_KW:
        return False
    return _draw_near(series, t)


def _heater_minutes(series: Series) -> list:
    """(t, flag) of minutes with a heater step on a hot tank."""
    return [(t, _heater_minute(series, t)) for t in series.times()]


def _heater_cycles(series: Series, now: int, taken: list, price: Callable) -> list:
    """Heater steps on a hot tank outside every bottom cycle."""
    flagged = _heater_minutes(series)
    cycles = []
    for a, b in _spans([f for _, f in flagged], 2):
        start, end = flagged[a][0], flagged[b][0]
        if b - a + 1 < MIN_STEP_MIN or any(c.start <= end and start <= c.end for c in taken):
            continue
        open_ = now - end < CLOSED_AFTER_MIN * MIN_S
        cycle = _build(series, start, end, open_, "heater", price)
        if cycle is not None:
            cycles.append(cycle)
    return cycles


def find_cycles(
    minutes: list, now: int, price: Callable[[int], Optional[float]] = lambda t: None
) -> list:
    """Every cycle in the minutes, in time order. price gives EUR/kWh."""
    series = Series(minutes)
    bottom = _bottom_cycles(series, now, price)
    heater = _heater_cycles(series, now, bottom, price)
    return sorted(bottom + heater, key=lambda c: c.start)


def lost_on_minutes(cycle: Cycle, is_on: Callable[[int], bool], kind: str, cap_min: int) -> int:
    """Program ON minutes the cycle took from the house. A regular run is
    capped at the pump's hot water time; a boost is counted whole."""
    if kind == "heater_run":
        return 0
    minutes = sum(1 for t in range(cycle.start, cycle.end + MIN_S, MIN_S) if is_on(t))
    return min(minutes, cap_min) if kind == "regular" else minutes


def start_level(cycles: list, count: int = 5) -> Optional[float]:
    """Median bottom of the last count slow-cooling regular starts."""
    starts = [c.start_bottom for c in cycles if c.slow_start and c.start_bottom is not None]
    return statistics.median(starts[-count:]) if starts else None
