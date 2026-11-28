"""Offline replays of the hot water controller (design 10, section 5).

Everything here reads the simulation dataset (gzipped CSV, see
scripts/sim/build_dataset.py) and writes nothing to InfluxDB or a relay.
"""

from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from src.control import hot_water_boost as hb
from src.control import hot_water_detect as hd
from src.monitoring import pump_health as ph

TOP = "Käyttövesi ylh"
BOTTOM = "Käyttövesi alh"


def load_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0)
    frame.index = pd.to_datetime(frame.index, utc=True, format="ISO8601")
    return frame


def minute_frame(tank: pd.DataFrame, em3: pd.DataFrame) -> pd.DataFrame:
    """One row per UTC minute: top, bottom, p1..p3 in kW."""
    t = tank[[TOP, BOTTOM]].resample("1min").mean()
    t.columns = ["top", "bottom"]
    p = em3[["phase1_power", "phase2_power", "phase3_power"]].resample("1min").mean() / 1000
    p.columns = ["p1", "p2", "p3"]
    return t.join(p, how="outer")


def behind_minutes(battery: pd.DataFrame) -> dict:
    """{minute start: kW} behind the 3EM, as run_pump_health reads it."""
    missing = [c for c in ph.BEHIND_SIGN if c not in battery.columns]
    if missing:
        raise ValueError(f"battery_minute lacks {missing}: rebuild the dataset")
    signs = pd.Series(ph.BEHIND_SIGN)
    net = (battery[list(signs.index)] * signs).sum(axis=1, min_count=len(signs)) / 1000
    net = net.resample("1min").mean().dropna()
    return {int(t.timestamp()): float(v) for t, v in net.items()}


def minutes(frame: pd.DataFrame) -> list:
    out = []
    columns = frame[["top", "bottom", "p1", "p2", "p3"]]
    for stamp, top, bottom, p1, p2, p3 in columns.itertuples():
        t = None if pd.isna(top) else float(top)
        b = None if pd.isna(bottom) else float(bottom)
        phases = None if pd.isna(p1) or pd.isna(p2) or pd.isna(p3) else (p1, p2, p3)
        if t is None and b is None and phases is None:
            continue
        out.append(hd.Minute(int(stamp.timestamp()), t, b, phases))
    return out


def reference_runs(frame: pd.DataFrame) -> list:
    """Run starts by the runs_window.py algorithm: the tank top's rise on
    2-min means. Independent of the detector, which uses the bottom."""
    top = frame["top"].resample("2min").mean().dropna()
    rising = top.rolling(3, center=True).mean().diff() > 0.04
    groups = (rising != rising.shift()).cumsum()
    starts = []
    for _, run in top[rising].groupby(groups[rising]):
        if len(run) < 3:
            continue
        begin = run.index[0] - pd.Timedelta(minutes=4)
        if run.iloc[-1] - top.asof(begin) < 1.5:
            continue
        starts.append(int(begin.timestamp()))
    return starts


def _segment(start: str, anchor: str, days: float) -> tuple:
    return (hb.parse_utc(start), hb.parse_utc(anchor), days)


# The pump timer as it stood (06 timer model): (from, anchor, interval days)
HISTORY_ANCHORS = [
    # The 14-day Friday grid, about 10:00 UTC+2
    _segment("2025-01-01T00:00:00Z", "2026-10-09T08:00:00Z", 14),
    # Interval 1 day from Sat 2026-11-14 10:15 local
    _segment("2026-11-14T08:15:00Z", "2026-10-09T08:00:00Z", 1),
    # Main switch restart Mon 2026-11-16 about 07:00 local, interval 6 days
    _segment("2026-11-16T05:00:00Z", "2026-11-16T05:00:00Z", 6),
]


def boost_settings(anchor_utc: int, days: float) -> hb.BoostSettings:
    """The cutover boost settings with another anchor and interval."""
    return hb.BoostSettings(
        anchor_utc=anchor_utc,
        interval_s=int(days * 86400),
        slot_s=180 * 60,
        trigger_attempt_s=30 * 60,
        retry_gap_s=30 * 60,
        unexpected_after_s=36 * 3600 if days > 2 else int(days * 86400) // 2,
    )


def settings_at(t: int, segments: list) -> hb.BoostSettings:
    current = [s for s in segments if s[0] <= t]
    _, anchor, days = current[-1]
    return boost_settings(anchor, days)


def label_history(records: list, segments: list) -> list:
    """Label each record on the timer that stood at its start."""
    used: set = set()
    out = []
    for r in sorted(records, key=lambda x: x.start):
        labelled = hb.label([r], settings_at(r.start, segments), frozenset(used))[0]
        if labelled.expiry is not None:
            used.add(labelled.expiry)
        out.append(labelled)
    return out


def on_since_at(commands: pd.DataFrame) -> Callable[[int], Optional[int]]:
    """For a time, the start of the pump's ON state then, or None."""
    rows = [(int(t.timestamp()), str(c)) for t, c in zip(commands.index, commands["command"])]

    def at(t: int) -> Optional[int]:
        since = None
        for when, command in rows:
            if when > t:
                break
            if command == "ON" and since is None:
                since = when
            elif command != "ON":
                since = None
        return since

    return at


def health_replay(
    mins: list, behind: dict, commands: pd.DataFrame, outdoor: dict, settings
) -> list:
    """(time, verdict) of each 5-min pump health run over the minutes."""
    on_since = on_since_at(commands)
    first, last = mins[0].t + ph.BASE_WINDOW_S, mins[-1].t
    out = []
    by_t = {m.t - m.t % 60: m for m in mins}
    for now in range(first - first % 300, last, 300):
        window = [by_t[t] for t in range(now - ph.BASE_WINDOW_S, now + 1, 60) if t in by_t]
        hour = now - now % 3600
        temp = outdoor[hour] if hour in outdoor else None
        out.append((now, ph.judge(window, behind, now, on_since(now), temp, settings)))
    return out


KNOWN_BOOSTS = (
    "2026-10-23 20:28",
    "2026-11-06 12:56",
    "2026-11-14 13:06",
    "2026-11-15 22:26",
    "2026-11-22 09:28",
)
HEATER_PULSES = ("2026-11-14 19:42", "2026-11-14 20:10", "2026-11-14 20:34", "2026-11-14 20:49")
NOT_HOT_WATER = (("2026-11-17 00:02", 20), ("2026-11-17 05:02", 20), ("2026-11-20 09:04", 12))


def local_epoch(text: str) -> int:
    return int(pd.Timestamp(text, tz="Europe/Helsinki").timestamp())


def near(cycles: list, t: int, within_s: int = 300) -> list:
    return [c for c in cycles if abs(c.start - t) <= within_s or c.start <= t <= c.end]


def r1_gates(frame: pd.DataFrame, cycles: list, labelled: list, car: list) -> dict:
    """The R1 gate values: reference match, boosts, heater pulses, false cycles."""
    reference = reference_runs(frame)
    matched, missed = match(reference, [c.start for c in cycles])
    kind = {r.start: r for r in labelled}
    boosts = {t: [kind[c.start] for c in near(cycles, local_epoch(t))] for t in KNOWN_BOOSTS}
    pulses = {
        t: [kind[c.start].kind for c in near(cycles, local_epoch(t), 600)] for t in HEATER_PULSES
    }
    false = {
        t: [c for c in cycles if local_epoch(t) <= c.start <= local_epoch(t) + m * 60]
        for t, m in NOT_HOT_WATER
    }
    in_car = [c for c in cycles if c.source == "heater" and any(a <= c.start <= b for a, b in car)]
    return {
        "reference": len(reference),
        "matched": len(matched),
        "missed": missed,
        "boosts": boosts,
        "pulses": pulses,
        "false": false,
        "heater_in_car": in_car,
    }


def r2_cases() -> list:
    """(case, expected, got) of the boost timer replay."""
    nov16 = boost_settings(hb.parse_utc("2026-11-16T05:00:00Z"), 6)
    grid = boost_settings(hb.parse_utc("2026-10-09T08:00:00Z"), 14)
    boost = hb.Record(local_epoch("2026-11-22 09:28"), 0, 45.6, 69.2, False)
    run = hb.Record(local_epoch("2026-11-22 00:04"), 0, 46.0, 51.0, False)
    on16 = hb.label([run, boost], nov16)[1]
    on_grid = hb.label([boost], grid)[0]
    summer = boost_settings(hb.parse_utc("2026-11-27T20:00:00Z"), 14)
    expiry = hb.next_expiry(local_epoch("2027-06-01 00:00"), summer)
    hour = pd.Timestamp(expiry, unit="s", tz="UTC").tz_convert("Europe/Helsinki").hour
    return [
        (
            "Nov 22 expiry on the Nov 16 anchor",
            "2026-11-22T05:00:00Z",
            hb.format_utc(nov16.anchor_utc + nov16.interval_s),
        ),
        (
            "Nov 22 boost on the Nov 16 anchor",
            "expected",
            "unexpected" if on16.unexpected else "expected",
        ),
        (
            "Nov 22 boost on the old grid",
            "unexpected",
            "unexpected" if on_grid.unexpected else "expected",
        ),
        ("Summer expiry of a Fri 22:00 winter anchor", "23", str(hour)),
    ]


def r2_missed(history: pd.DataFrame, day: str, trigger_acts: bool) -> tuple:
    """(missed expiry mailed at, labels after) for a missed boost of the old grid."""
    anchor = hb.parse_utc("2026-10-09T08:00:00Z") - 28 * 14 * 86400
    s = boost_settings(anchor, 14)
    rows = [
        hb.Record(int(t.timestamp()), int(t.timestamp()) + 1800, None, float(peak), False)
        for t, peak in zip(history.index, history["end_temp"])
    ]
    found = hb.expiry_at_or_before(local_epoch(f"{day} 12:00"), s)
    if found is None:
        raise ValueError(f"No expiry on {day}")
    e = found
    window = [r for r in rows if e - 86400 <= r.start <= e + 3 * 86400]
    labelled = hb.label(window, s)
    moment = hb.slot_end(e, s) if trigger_acts else hb.window_end(e, s)
    before = [r for r in labelled if r.start < moment]
    mailed = hb.missed(before, moment, s, trigger_acts)
    status = hb.status(labelled, e + 3 * 86400, s)
    # The window holds no cycles of the expiry before, so only e itself counts
    return (mailed if mailed == e else None), labelled, status.next_expiry


def record(cycle: hd.Cycle) -> hb.Record:
    return hb.Record(cycle.start, cycle.end, cycle.start_top, cycle.peak_top, cycle.heater)


def match(reference: list, found: list, within_s: int = 300) -> tuple:
    """(matched, missed starts) of the reference runs against the found starts."""
    matched, missed = [], []
    for start in reference:
        if any(abs(f - start) <= within_s for f in found):
            matched.append(start)
        else:
            missed.append(start)
    return matched, missed
