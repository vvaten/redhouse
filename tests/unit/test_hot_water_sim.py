"""The replay helpers of design 10 section 5, on fixture frames."""

import pandas as pd

from src.control import hot_water_boost as hb
from src.control import hot_water_detect as hd
from src.monitoring import pump_health as ph
from src.simulation import hot_water_sim as hs
from tests.unit.test_hot_water_detect import frame, local


def to_frame(minutes: list) -> pd.DataFrame:
    index = pd.to_datetime([m.t for m in minutes], unit="s", utc=True)
    return pd.DataFrame(
        {
            "top": [m.top for m in minutes],
            "bottom": [m.bottom for m in minutes],
            "p1": [m.phases[0] if m.phases else None for m in minutes],
            "p2": [m.phases[1] if m.phases else None for m in minutes],
            "p3": [m.phases[2] if m.phases else None for m in minutes],
        },
        index=index,
    )


def test_minutes_round_trip_a_frame():
    minutes = frame("regular")
    back = hs.minutes(to_frame(minutes))
    assert len(back) == len(minutes) and back[0].t == minutes[0].t


def test_minute_frame_joins_tank_and_phases():
    index = pd.to_datetime([0, 30, 60], unit="s", utc=True)
    tank = pd.DataFrame({hs.TOP: [50, 50, 51], hs.BOTTOM: [45, 45, 46]}, index=index)
    em3 = pd.DataFrame(
        {"phase1_power": [1000] * 3, "phase2_power": [0] * 3, "phase3_power": [0] * 3}, index=index
    )
    joined = hs.minute_frame(tank, em3)
    assert (
        list(joined.columns) == ["top", "bottom", "p1", "p2", "p3"] and joined["p1"].iloc[0] == 1.0
    )


def test_the_reference_finds_the_regular_run_and_the_detector_matches_it():
    minutes = frame("regular")
    reference = hs.reference_runs(to_frame(minutes))
    found = [c.start for c in hd.find_cycles(minutes, minutes[-1].t + 3600)]
    matched, missed = hs.match(reference, found)
    assert len(reference) == 1 and len(matched) == 1 and missed == []


def test_history_labels_follow_the_anchor_that_stood():
    records = [
        hb.Record(local("2026-11-14 13:08"), 0, 46.9, 69.9, False),
        hb.Record(local("2026-11-22 09:27"), 0, 45.6, 69.1, False),
    ]
    labelled = hs.label_history(records, hs.HISTORY_ANCHORS)
    assert [(r.kind, r.unexpected) for r in labelled] == [(hb.BOOST, False), (hb.BOOST, False)]


def test_the_health_replay_alerts_on_the_alarm_night():
    minutes = frame("alarm_boost")
    commands = pd.DataFrame(
        {"command": ["ON"]}, index=pd.to_datetime([local("2026-11-16 00:00")], unit="s", utc=True)
    )
    hour = local("2026-11-16 00:00")
    outdoor = {hour - 3600: 6.5, hour: 6.3}
    results = hs.health_replay(minutes, commands, outdoor, ph.HealthSettings(45, 8.0))
    alarms = [t for t, v in results if v.alarm]
    assert alarms and min(alarms) <= local("2026-11-16 00:50")
