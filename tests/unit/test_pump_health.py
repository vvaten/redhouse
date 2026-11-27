"""Pump health: the 2026-11-16 trip alarms, normal idles in ON do not."""

import csv
import datetime
import json

import pytest

import run_pump_health as rph
from src.control import hot_water_detect as hd
from src.monitoring import pump_health as ph
from tests.unit.test_hot_water_detect import FIXTURES, frame, local

SETTINGS = ph.HealthSettings(no_step_min=90, below_outdoor_c=8.0)
# The program put the pump ON at 00:00 local on 2026-11-16; it stopped at 02:20
TRIP_ON = local("2026-11-16 00:00")


def behind(name: str) -> dict:
    """{minute: kW} behind the 3EM in the fixture: battery minus solar."""
    with open(FIXTURES / f"{name}.csv", newline="") as f:
        return {
            int(r["t"]): float(r["battery"]) - float(r["solar"])
            for r in csv.DictReader(f)
            if r["battery"] and r["solar"]
        }


def trip(now: str, **kwargs) -> ph.Verdict:
    args = {"on_since": TRIP_ON, "outdoor_c": 6.3, "s": SETTINGS}
    args.update(kwargs)
    return ph.judge(frame("pump_idle_nov16"), behind("pump_idle_nov16"), local(now), **args)


def flat(n: int, l2: float = 0.1) -> list:
    return [hd.Minute(t * 60, 50.0, 50.0, (0.9, l2, 0.2)) for t in range(n)]


def zero(n: int) -> dict:
    return {t * 60: 0.0 for t in range(n)}


class TestTheTripNight:
    def test_alarms_90_min_after_the_pump_stopped(self):
        verdict = trip("2026-11-16 03:55")
        assert verdict.alarm, verdict
        assert verdict.reason == "ON and nothing runs"

    def test_no_alarm_before_90_idle_min(self):
        assert not trip("2026-11-16 03:30").alarm

    def test_no_alarm_while_the_boost_and_the_compressor_ran(self):
        for now in ("2026-11-16 01:30", "2026-11-16 02:15"):
            verdict = trip(now)
            assert not verdict.alarm and verdict.reason == "the pump runs", (now, verdict)

    def test_a_time_between_whole_minutes_still_judges(self):
        """The timer fires at :40 s; the minute data sits on whole minutes."""
        minutes, net = frame("pump_idle_nov16"), behind("pump_idle_nov16")
        now = local("2026-11-16 03:55") + 40
        assert ph.judge(minutes, net, now, TRIP_ON, 6.3, SETTINGS).alarm
        assert rph.minute_now(now) == local("2026-11-16 03:55")

    def test_the_quiet_base_is_the_house_alone(self):
        base = trip("2026-11-16 03:55").base_kw
        assert base is not None and 0.0 < base < 0.4


class TestSolarAndBattery:
    def test_a_sunny_day_does_not_pull_the_base_down(self):
        """On 2026-11-05 the panels gave 3.5 kW and raw L2 went to -0.7 kW."""
        minutes, net = frame("day_idle_nov05"), behind("day_idle_nov05")
        verdict = ph.judge(minutes, net, local("2026-11-05 13:00"), 0, 4.7, SETTINGS)
        assert verdict.base_kw is not None and verdict.base_kw > 0.0, verdict
        raw = ph.judge(minutes, {t: 0.0 for t in net}, local("2026-11-05 13:00"), 0, 4.7, SETTINGS)
        assert raw.base_kw is not None and raw.base_kw < 0.0

    def test_the_trip_still_alarms_after_a_day_of_solar_export(self):
        minutes = frame("pump_idle_nov16")
        net = behind("pump_idle_nov16")
        sunny = range(local("2026-11-15 11:00"), local("2026-11-15 13:00"), 60)
        shifted = [
            (
                hd.Minute(m.t, m.top, m.bottom, (m.phases[0], m.phases[1] - 1.0, m.phases[2]))
                if m.t in sunny and m.phases is not None
                else m
            )
            for m in minutes
        ]
        net.update({t: net[t] - 3.0 for t in sunny if t in net})
        verdict = ph.judge(shifted, net, local("2026-11-16 03:55"), TRIP_ON, 6.3, SETTINGS)
        assert verdict.alarm, verdict

    def test_battery_charging_is_taken_off_l2(self):
        rows = flat(250) + [hd.Minute(t * 60, 50.0, 50.0, (0.9, 0.9, 0.2)) for t in range(250, 400)]
        charging = zero(250) | {t * 60: 2.4 for t in range(250, 400)}
        assert ph.judge(rows, charging, 399 * 60, 0, 0.0, SETTINGS).alarm
        assert not ph.judge(rows, zero(400), 399 * 60, 0, 0.0, SETTINGS).alarm

    def test_late_checkwatt_minutes_are_skipped(self):
        """CheckWatt arrives minutes late: the last minutes have no battery data."""
        rows = flat(400)
        late = {t: v for t, v in zero(400).items() if t <= 392 * 60}
        assert ph.judge(rows, late, 399 * 60, 0, 0.0, SETTINGS).alarm

    def test_no_checkwatt_data_judges_nothing(self):
        verdict = ph.judge(flat(400), {}, 399 * 60, 0, 0.0, SETTINGS)
        assert not verdict.alarm and verdict.reason == "too little data for the base"


class TestNormalIdles:
    def test_the_77_min_daytime_idle_of_nov_5_gives_no_alarm(self):
        minutes, net = frame("day_idle_nov05"), behind("day_idle_nov05")
        on = local("2026-11-05 11:00")
        reasons = set()
        for now in range(local("2026-11-05 12:30"), local("2026-11-05 13:01"), 300):
            verdict = ph.judge(minutes, net, now, on, 4.7, SETTINGS)
            assert not verdict.alarm, (now, verdict)
            reasons.add(verdict.reason)
        assert reasons == {"the pump runs"}

    def test_normal_on_hours_give_no_alarm(self):
        now = local("2026-11-17 05:45")
        minutes = frame("stages_nov17")
        net = {m.t - m.t % 60: 0.0 for m in minutes}
        verdict = ph.judge(minutes, net, now, now - 6000, 2.0, SETTINGS)
        assert not verdict.alarm and verdict.reason == "the pump runs", verdict

    def test_a_blip_does_not_end_an_idle_stretch(self):
        rows = flat(400)
        for t in (350, 351):
            rows[t] = hd.Minute(t * 60, 50.0, 50.0, (0.9, 1.1, 0.2))
        assert ph.judge(rows, zero(400), 399 * 60, 0, 0.0, SETTINGS).alarm
        rows[352] = hd.Minute(352 * 60, 50.0, 50.0, (0.9, 1.1, 0.2))
        assert not ph.judge(rows, zero(400), 399 * 60, 0, 0.0, SETTINGS).alarm


class TestGuards:
    def test_not_on_for_the_whole_window(self):
        verdict = trip("2026-11-16 03:55", on_since=local("2026-11-16 02:30"))
        assert not verdict.alarm and "not ON" in verdict.reason

    def test_mild_weather(self):
        verdict = trip("2026-11-16 03:55", outdoor_c=9.0)
        assert not verdict.alarm and "cold" in verdict.reason

    def test_no_outdoor_reading(self):
        verdict = trip("2026-11-16 03:55", outdoor_c=None)
        assert not verdict.alarm and verdict.reason == "no outdoor reading"

    def test_too_little_data_for_the_base(self):
        verdict = ph.judge(flat(200), zero(200), 199 * 60, 0, 0.0, SETTINGS)
        assert not verdict.alarm and verdict.reason == "too little data for the base"

    def test_too_little_data_in_the_window(self):
        rows = [m for m in flat(500) if m.t < 410 * 60 or m.t % 1200 == 0]
        verdict = ph.judge(rows, zero(500), 499 * 60, 0, 0.0, SETTINGS)
        assert not verdict.alarm and verdict.reason == "too little data in the window"

    def test_settings_need_both_keys(self):
        with pytest.raises(ValueError):
            ph.load_health_settings({"no_step_min": 90})
        assert ph.load_health_settings({"no_step_min": 90, "below_outdoor_c": 8}) == SETTINGS


class Rec:
    def __init__(self, t, field, value):
        self._t, self._f, self._v = t, field, value

    def get_time(self):
        return datetime.datetime.fromtimestamp(self._t, datetime.timezone.utc)

    def get_field(self):
        return self._f

    def get_value(self):
        return self._v


class TestWrapper:
    def test_pump_on_since_reads_the_state_file(self, tmp_path):
        path = tmp_path / "pump_state.json"
        path.write_text(json.dumps({"last_command": "ON", "on_since": 100}))
        assert rph.pump_on_since(path, [], 200) == 100
        path.write_text(json.dumps({"last_command": "ALE", "on_since": None}))
        assert rph.pump_on_since(path, [], 200) is None
        path.write_text(json.dumps({"last_command": "ON"}))
        assert rph.pump_on_since(path, [], 200) is None
        assert rph.pump_on_since(tmp_path / "missing.json", [], 200) is None

    def test_an_evu_pulse_does_not_restart_the_window(self, tmp_path):
        """The pulse resets the state's on_since; the program block counts."""
        path = tmp_path / "pump_state.json"
        path.write_text(json.dumps({"last_command": "ON", "on_since": 7000}))
        entries = [(0, "ALE"), (3600, "ON"), (7200, "ON"), (10800, "ALE")]
        assert rph.pump_on_since(path, entries, 9000) == 3600

    def test_program_on_since(self):
        entries = [(0, "ON"), (3600, "ALE"), (7200, "ON"), (10800, "ON")]
        assert rph.program_on_since(entries, 11000) == 7200
        assert rph.program_on_since(entries, 4000) is None
        assert rph.program_on_since([], 4000) is None

    def test_one_mail_per_6_h(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rph, "ALERT_STATE", tmp_path / "a.json")
        assert rph.should_mail(1000)
        (tmp_path / "a.json").write_text(json.dumps({"sent": 1000}))
        assert not rph.should_mail(1000 + 6 * 3600 - 1)
        assert rph.should_mail(1000 + 6 * 3600)

    def test_behind_is_charge_minus_discharge_minus_solar_in_kw(self):
        class Table:
            records = [
                Rec(60, "BatteryCharge", 2400.0),
                Rec(60, "SolarYield", 600.0),
                Rec(120, "BatteryDischarge", 600.0),
            ]

        class Influx:
            def query_with_retry(self, flux):
                assert "SolarYield" in flux and "BatteryDischarge" in flux
                return [Table()]

        class Config:
            influxdb_bucket_checkwatt = "checkwatt"

        got = rph.behind_kw(Influx(), Config(), 0, 180)
        assert got == {60: pytest.approx(1.8), 120: pytest.approx(-0.6)}
