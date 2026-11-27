"""The replay helpers of design 10 section 5, on fixture frames."""

import pandas as pd
import pytest

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
    minutes = frame("pump_idle_nov16")
    commands = pd.DataFrame(
        {"command": ["ALE", "ON"]},
        index=pd.to_datetime(
            [local("2026-11-15 03:00"), local("2026-11-16 00:00")], unit="s", utc=True
        ),
    )
    outdoor = {local("2026-11-16 00:00") + h * 3600: 6.3 for h in range(-24, 6)}
    net = {m.t - m.t % 60: 0.0 for m in minutes}
    results = hs.health_replay(minutes, net, commands, outdoor, ph.HealthSettings(90, 8.0))
    alarms = [t for t, v in results if v.alarm]
    assert alarms and local("2026-11-16 03:45") <= min(alarms) <= local("2026-11-16 03:55")


def test_behind_minutes_is_charge_minus_discharge_minus_solar():
    index = pd.to_datetime(["2026-11-16T00:00:00Z", "2026-11-16T00:01:00Z"], utc=True)
    battery = pd.DataFrame(
        {
            "BatteryCharge": [2400.0, 0.0],
            "BatteryDischarge": [0.0, 600.0],
            "SolarYield": [600.0, 0.0],
        },
        index=index,
    )
    got = hs.behind_minutes(battery)
    t0 = int(index[0].timestamp())
    assert got == {t0: pytest.approx(1.8), t0 + 60: pytest.approx(-0.6)}
    with pytest.raises(ValueError):
        hs.behind_minutes(battery.drop(columns=["SolarYield"]))
