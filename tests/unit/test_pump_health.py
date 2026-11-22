"""Pump health: the 2026-11-16 alarm night gives an alert, a boost does not."""

import json

import pytest

import run_pump_health as rph
from src.control import hot_water_detect as hd
from src.monitoring import pump_health as ph
from tests.unit.test_hot_water_detect import frame, local

SETTINGS = ph.HealthSettings(no_compressor_min=45, below_outdoor_c=8.0)
# The program put the pump ON at 00:00 local on 2026-11-16
ON_SINCE = local("2026-11-16 00:00")


class TestAlarmNight:
    def test_the_house_part_alerts_by_00_50(self):
        minutes = frame("alarm_boost")
        verdict = ph.judge(minutes, local("2026-11-16 00:50"), ON_SINCE, 6.3, SETTINGS)
        assert verdict.alarm, verdict

    def test_the_boost_part_gives_no_alert(self):
        minutes = [m for m in frame("alarm_boost") if m.t <= local("2026-11-15 23:40")]
        on_since = local("2026-11-15 22:20")
        verdict = ph.judge(minutes, local("2026-11-15 23:40"), on_since, 6.3, SETTINGS)
        assert not verdict.alarm and "boost" in verdict.reason

    def test_a_normal_boost_gives_no_alert(self):
        minutes = frame("boost")
        now = local("2026-11-22 10:30")
        verdict = ph.judge(minutes, now, now - 3600, 2.0, SETTINGS)
        assert not verdict.alarm

    def test_normal_on_hours_give_no_alert(self):
        minutes = frame("stages_nov17")
        for t in ("2026-11-17 00:50", "2026-11-17 05:40"):
            verdict = ph.judge(minutes, local(t), local(t) - 3000, 2.0, SETTINGS)
            assert not verdict.alarm, (t, verdict)


class TestGuards:
    def test_not_on_for_the_whole_window(self):
        verdict = ph.judge(frame("alarm_boost"), local("2026-11-16 00:40"), ON_SINCE, 6.3, SETTINGS)
        assert not verdict.alarm and "not ON" in verdict.reason

    def test_mild_weather(self):
        verdict = ph.judge(frame("alarm_boost"), local("2026-11-16 00:50"), ON_SINCE, 9.0, SETTINGS)
        assert not verdict.alarm and "cold" in verdict.reason

    def test_no_outdoor_reading(self):
        verdict = ph.judge(
            frame("alarm_boost"), local("2026-11-16 00:50"), ON_SINCE, None, SETTINGS
        )
        assert not verdict.alarm

    def test_too_little_data(self):
        minutes = [hd.Minute(0, 50.0, 50.0, (1.0, 1.0, 1.0))]
        assert not ph.judge(minutes, 3600, 0, 0.0, SETTINGS).alarm

    def test_settings_need_both_keys(self):
        with pytest.raises(ValueError):
            ph.load_health_settings({"no_compressor_min": 45})

    def test_heater_only_wants_a_balanced_full_step(self):
        base = (0.8, 0.2, 0.2)
        assert ph.heater_only((2.9, 2.4, 2.4), base)
        assert not ph.heater_only((2.9, 2.05, 2.2), base)
        assert not ph.heater_only((1.8, 1.2, 1.2), base)


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
