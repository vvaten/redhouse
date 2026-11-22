"""The cycle log of the hot water tick: new cycles against the written
history, their labels, and the start level (pure code)."""

import statistics
from dataclasses import dataclass, replace
from typing import Callable, Optional

from src.control import hot_water_boost as hb
from src.control import hot_water_detect as hd
from src.control import hot_water_io as hio

# A detected cycle this close to a written one is the same cycle
SAME_CYCLE_S = 10 * 60
START_LEVEL_COUNT = 5


@dataclass(frozen=True)
class CycleView:
    # Every labelled record, written and new, in time order
    records: list
    open: Optional[hb.Record]
    # (record, cycle) of the closed cycles not written yet
    new: list
    # (history row, relabelled record)
    relabelled: list
    start_level: Optional[float]
    start_level_count: int
    last_start: Optional[int]


def to_record(cycle: hd.Cycle) -> hb.Record:
    return hb.Record(cycle.start, cycle.end, cycle.start_top, cycle.peak_top, cycle.heater)


def _known(cycle: hd.Cycle, written: list) -> bool:
    return any(
        abs(cycle.start - r.start) <= SAME_CYCLE_S or (r.start <= cycle.start <= r.end)
        for r in written
    )


def _starts(rows: list, new: list) -> list:
    """(start, bottom) of the slow-cooling regular starts, oldest first."""
    found = [
        (int(row["start"]), float(row["start_bottom"]))
        for row in rows
        if row["kind"] == hb.REGULAR and bool(row["slow_start"]) and float(row["start_bottom"]) > 0
    ]
    for rec, cycle in new:
        if rec.kind == hb.REGULAR and cycle.slow_start and cycle.start_bottom is not None:
            found.append((cycle.start, float(cycle.start_bottom)))
    return sorted(found)


def _used(written: list) -> frozenset:
    """Expiries a written cycle took, a relabelled one included."""
    return frozenset(r.expiry for r in written if r.expiry is not None)


def _relabelled(rows: list, records: list, settings: hb.BoostSettings) -> list:
    """(row, record) of written boosts that are now known to be regular runs."""
    by_start = {int(row["start"]): row for row in rows}
    fixed = [r for r in hb.relabel_stalled(records, settings) if r.start in by_start]
    return [(by_start[r.start], r) for r in fixed if by_start[r.start]["kind"] != hb.REGULAR]


def _continued(cycles: list, written: list) -> list:
    """Open cycles that grew out of a written one, as that record, extended."""
    out = []
    for c in cycles:
        if not c.open:
            continue
        for r in written:
            if _known(c, [r]):
                peaks = [p for p in (r.peak_top, c.peak_top) if p is not None]
                out.append(replace(r, end=c.end, peak_top=max(peaks) if peaks else None))
    return out


def build_view(rows: list, cycles: list, settings: hb.BoostSettings) -> CycleView:
    written = [hio.to_record(row) for row in rows]
    fresh = [c for c in cycles if not _known(c, written)]
    labelled = hb.label([to_record(c) for c in fresh], settings, _used(written))
    pairs = list(zip(labelled, fresh))
    new = [(r, c) for r, c in pairs if not c.open]
    opened = _continued(cycles, written) + [r for r, c in pairs if c.open]
    records = sorted(written + labelled, key=lambda r: r.start)
    relabelled = _relabelled(rows, records, settings)
    starts = [b for _, b in _starts(rows, new)][-START_LEVEL_COUNT:]
    return CycleView(
        records=records,
        open=opened[-1] if opened else None,
        new=new,
        relabelled=relabelled,
        start_level=statistics.median(starts) if starts else None,
        start_level_count=len(starts),
        last_start=records[-1].start if records else None,
    )


def cause_of(start: int, spans: list) -> str:
    """The rule whose relay ON began before the cycle start, or the pump."""
    for begin, end, rule in spans:
        if begin - hd.MIN_S <= start <= end + 5 * hd.MIN_S:
            return str(rule)
    return "pump"


def cycle_points(view: CycleView, is_on: Callable, cap_min: int, spans: list) -> list:
    points = []
    for rec, cycle in view.new:
        lost = hd.lost_on_minutes(cycle, is_on, rec.kind, cap_min)
        points.append(hio.cycle_point(rec, cycle, lost, cause_of(cycle.start, spans)))
    for row, rec in view.relabelled:
        points.append(hio.relabel_point(row, rec))
    return points
