"""The legionella boost timer (pure code, all arithmetic in UTC epochs).

The pump's timer expires at the last pump restart (the anchor) plus a whole
number of intervals, on a clock with no DST. The first hot water cycle after
an expiry is the boost.
"""

import datetime
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

from src.common.atomic_json import write_json

ANCHOR_FILE = Path(__file__).resolve().parents[2] / "data" / "hot_water_boost.json"
REGULAR = "regular"
BOOST = "legionella_boost"
HEATER = "heater_run"
KINDS = (REGULAR, BOOST, HEATER)
HOT_TOP_C = 55.0
BOOST_DONE_C = 62.0
# A boost still under BOOST_DONE_C this long after its start has stalled
INCOMPLETE_AFTER_S = 3 * 3600


@dataclass(frozen=True)
class BoostSettings:
    anchor_utc: int
    interval_s: int
    slot_s: int
    trigger_attempt_s: int
    retry_gap_s: int
    unexpected_after_s: int


@dataclass(frozen=True)
class Record:
    """A cycle as the boost timer sees it."""

    start: int
    end: int
    start_top: Optional[float]
    peak_top: Optional[float]
    heater: bool
    kind: str = ""
    expiry: Optional[int] = None
    unexpected: bool = False

    @property
    def stalled(self) -> bool:
        return self.kind == BOOST and (self.peak_top is None or self.peak_top < BOOST_DONE_C)


def parse_utc(text: str) -> int:
    """An ISO time with a zone, such as 2026-11-27T20:00:00Z, as epoch seconds."""
    stamp = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError(f"{text} has no zone")
    return int(stamp.timestamp())


def format_utc(epoch: int) -> str:
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def load_boost_settings(section: dict, anchor_utc: Optional[int] = None) -> BoostSettings:
    """From the hot_water.boost section; an anchor file value wins over the yaml."""
    keys = (
        "anchor_utc",
        "interval_days",
        "slot_minutes",
        "trigger_attempt_min",
        "retry_gap_min",
        "unexpected_after_h",
    )
    missing = [k for k in keys if k not in section]
    if missing:
        raise ValueError(f"config.yaml hot_water.boost lacks {missing}")
    settings = BoostSettings(
        anchor_utc=parse_utc(str(section["anchor_utc"])) if anchor_utc is None else anchor_utc,
        interval_s=int(float(section["interval_days"]) * 86400),
        slot_s=int(section["slot_minutes"]) * 60,
        trigger_attempt_s=int(section["trigger_attempt_min"]) * 60,
        retry_gap_s=int(section["retry_gap_min"]) * 60,
        unexpected_after_s=int(float(section["unexpected_after_h"]) * 3600),
    )
    if settings.interval_s <= settings.slot_s + settings.unexpected_after_s:
        raise ValueError("hot_water.boost interval must exceed the slot and the unexpected wait")
    return settings


def expiry_at_or_before(t: int, s: BoostSettings) -> Optional[int]:
    """The latest expiry at or before t; none before the first interval."""
    if t < s.anchor_utc + s.interval_s:
        return None
    return s.anchor_utc + (t - s.anchor_utc) // s.interval_s * s.interval_s


def next_expiry(t: int, s: BoostSettings) -> int:
    """The first expiry after t."""
    last = expiry_at_or_before(t, s)
    return s.anchor_utc + s.interval_s if last is None else last + s.interval_s


def slot_end(expiry: int, s: BoostSettings) -> int:
    return expiry + s.slot_s


def window_end(expiry: int, s: BoostSettings) -> int:
    """An expiry with no cycle by now is no longer pending."""
    return expiry + s.slot_s + s.unexpected_after_s


def _hot_start(r: Record) -> bool:
    return r.start_top is not None and r.start_top >= HOT_TOP_C


def label(records: list, s: BoostSettings, used: frozenset = frozenset()) -> list:
    """The records with kind, expiry and the unexpected flag, in time order.
    used holds the expiries that earlier records already took."""
    taken = set(used)
    out = []
    for r in sorted(records, key=lambda x: x.start):
        e = expiry_at_or_before(r.start, s)
        if e is not None and e not in taken and r.start <= window_end(e, s):
            taken.add(e)
            out.append(replace(r, kind=BOOST, expiry=e, unexpected=False))
        elif _hot_start(r) or r.heater:
            # The compressor cannot heat a tank over 55 C, so only the heater did
            out.append(replace(r, kind=HEATER, expiry=None, unexpected=False))
        elif r.peak_top is not None and r.peak_top > HOT_TOP_C:
            # A boost in the slot of a taken expiry is a retry of that boost
            retry = e is not None and r.start < slot_end(e, s)
            out.append(replace(r, kind=BOOST, expiry=e if retry else None, unexpected=not retry))
        else:
            out.append(replace(r, kind=REGULAR, expiry=None, unexpected=False))
    return out


@dataclass(frozen=True)
class BoostStatus:
    expiry: Optional[int]
    pending: bool
    in_slot: bool
    # From the expiry until a cycle takes it or the slot ends: rules 4 and 7 wait
    blocks_runs: bool
    boost: Optional[Record]
    next_expiry: int


def status(records: list, now: int, s: BoostSettings) -> BoostStatus:
    e = expiry_at_or_before(now, s)
    nxt = next_expiry(now, s)
    if e is None:
        return BoostStatus(None, False, False, False, None, nxt)
    taken = [r for r in records if r.kind == BOOST and r.expiry == e]
    done = [r for r in taken if not r.stalled]
    boost = done[0] if done else (taken[0] if taken else None)
    pending = boost is None and now <= window_end(e, s)
    in_slot = e <= now < slot_end(e, s)
    return BoostStatus(e, pending, in_slot, pending and in_slot, boost, nxt)


def missed(records: list, now: int, s: BoostSettings, trigger_acts: bool) -> Optional[int]:
    """The expiry whose boost is missed now, or None. With the trigger in act
    it is missed at the slot end with no cycle in the slot; without it only
    when the wait after the slot is over too."""
    last = expiry_at_or_before(now, s)
    candidates = [] if last is None else [last, last - s.interval_s]
    for e in candidates:
        if e < s.anchor_utc + s.interval_s:
            continue
        in_slot = [r for r in records if e <= r.start < slot_end(e, s)]
        taken = [r for r in records if r.kind == BOOST and r.expiry == e]
        if trigger_acts and now >= slot_end(e, s) and not in_slot:
            return e
        if not trigger_acts and now > window_end(e, s) and not taken:
            return e
    return None


def incomplete(records: list, now: int, top_max: Callable[[int, int], Optional[float]]) -> list:
    """Boosts that took an expiry and whose tank top is still under 62 C
    3 h after their start. top_max(start, stop) is the top's maximum."""
    out = []
    for r in records:
        if r.kind != BOOST or r.expiry is None or now < r.start + INCOMPLETE_AFTER_S:
            continue
        peaks = [p for p in (r.peak_top, top_max(r.start, r.start + INCOMPLETE_AFTER_S)) if p]
        if not peaks or max(peaks) < BOOST_DONE_C:
            out.append(r)
    return out


def _proves_regular(r: Record, u: Record, s: BoostSettings) -> bool:
    """u, a later boost, shows that the stalled r was a regular run: a retry
    of the same expiry that heated, or an unexpected boost in r's interval."""
    if u.kind != BOOST or u.start <= r.start or r.expiry is None:
        return False
    if u.expiry == r.expiry and not u.stalled:
        return True
    return u.unexpected and r.expiry <= u.start < r.expiry + s.interval_s


def relabel_stalled(records: list, s: BoostSettings) -> list:
    """Stalled boosts that a later boost proves were regular runs, returned
    relabelled as regular. They keep their expiry, so it stays taken."""
    return [
        replace(r, kind=REGULAR)
        for r in records
        if r.stalled and any(_proves_regular(r, u, s) for u in records)
    ]


def read_anchor(path: Path) -> Optional[int]:
    """The anchor from its file (set by hotwater anchor), or None."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    if not isinstance(data, dict) or "anchor_utc" not in data:
        raise ValueError(f"{path} lacks anchor_utc")
    return parse_utc(str(data["anchor_utc"]))


def slots_between(start: int, stop: int, s: BoostSettings, length_s: int) -> list:
    """(begin, end) of each expiry's first length_s that overlaps [start, stop)."""
    out = []
    e = expiry_at_or_before(start - length_s, s)
    e = s.anchor_utc + s.interval_s if e is None else e
    while e < stop:
        if e + length_s > start:
            out.append((e, e + length_s))
        e += s.interval_s
    return out


def write_anchor(path: Path, anchor_utc: int, entered_at: int) -> None:
    write_json(path, {"anchor_utc": format_utc(anchor_utc), "entered_utc": format_utc(entered_at)})
