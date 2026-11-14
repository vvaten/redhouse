"""Tests for the load guard wrapper: meter read, minute log, dry run."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

REPO = Path(__file__).resolve().parents[2]


def _wrapper():
    path = REPO / "run_load_guard.py"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _wrapper()
STATUS = {
    "emeters": [
        {"power": 1000.0, "total": 5000.0, "total_returned": 100.0},
        {"power": 2000.0, "total": 6000.0, "total_returned": 0.0},
        {"power": -500.0, "total": 7000.0, "total_returned": 900.0},
    ]
}


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "MINUTE_LOG", tmp_path / "guard_minutes.json")
    monkeypatch.setattr(guard, "CAP_STATE", tmp_path / "power_cap_state.json")
    monkeypatch.setattr(guard, "GARAGE_STATE", tmp_path / "garage_state.json")
    monkeypatch.setattr(guard, "DATA", tmp_path)
    return tmp_path


def _response(body):
    return Mock(raise_for_status=Mock(), json=Mock(return_value=body))


def test_the_meter_gives_phase_power_and_the_net_counter():
    with patch.object(guard.requests, "get", return_value=_response(STATUS)):
        sample = guard.read_meter("http://em3", 100.0)
    assert sample.phase_kw == (1.0, 2.0, -0.5)
    assert sample.net_wh == pytest.approx(4900 + 6000 + 6100)
    assert sample.import_wh == pytest.approx(18000)


def test_a_meter_without_three_phases_is_refused():
    with patch.object(guard.requests, "get", return_value=_response({"emeters": []})):
        with pytest.raises(ValueError):
            guard.read_meter("http://em3", 100.0)


def test_the_minute_log_keeps_only_the_last_70_minutes(data):
    now = 1_800_000_000.0
    old = guard.lg.Sample(now - 71 * 60, (1.0, 1.0, 1.0), 10.0, 10.0)
    new = guard.lg.Sample(now - 60, (1.0, 1.0, 1.0), 20.0, 20.0)
    guard.save_minutes([old, new])
    assert guard.load_minutes(now) == [new]


def test_a_broken_minute_log_starts_empty(data):
    (data / "guard_minutes.json").write_text("{trunc")
    assert guard.load_minutes(1_800_000_000.0) == []


def _garage(data, on=True, age=60, reason="plan_heat", now=1_800_000_000.0):
    state = {"on": on, "t": now - age, "reason": reason}
    (data / "garage_state.json").write_text(json.dumps(state))


@pytest.mark.parametrize(
    "on, age, reason, expected",
    [
        (True, 60, "plan_heat", True),
        (True, 3600, "plan_heat", False),
        (False, 60, "plan_coast", False),
        (True, 60, "under_floor", False),
        (True, 60, "stale_reading", False),
    ],
)
def test_only_a_fresh_planned_heat_can_be_shed(data, monkeypatch, on, age, reason, expected):
    monkeypatch.setattr(guard, "OVERRIDES", data / "load_overrides.json")
    _garage(data, on, age, reason)
    assert guard.garage_sheddable(1_800_000_000.0) is expected


def test_a_held_garage_is_not_shed_again(data, monkeypatch):
    monkeypatch.setattr(guard, "OVERRIDES", data / "load_overrides.json")
    now = 1_800_000_000.0
    _garage(data)
    guard.lo.write_overrides(
        data / "load_overrides.json",
        [guard.lo.Override("garage_heater", "OFF", now - 60, now + 600, "x")],
        now,
    )
    assert not guard.garage_sheddable(now)


def test_the_pump_command_comes_from_its_state_file(data):
    (data / "pump_state.json").write_text(json.dumps({"last_command": "ON"}))
    assert guard.pump_command(data / "pump_state.json") == "ON"
    assert guard.pump_command(data / "none.json") is None


class TestMain:
    @pytest.fixture
    def run(self, data, monkeypatch):
        config = Mock(
            power_cap_section={
                "night_factor": 0.8,
                "night_start_hour": 22,
                "night_end_hour": 7,
                "month_floor_kw": 7.5,
                "plan_margin_kw": 0.3,
                "guard_margin_kw": 0.1,
                "tariff_eur_per_kw": 1.97,
                "max_import_kw": 34.5,
            }
        )
        config.load_guard_section = {"mode": "dry_run", "pump_ale_kw": 3.0, "pump_evu_kw": 1.0}
        config.garage_section = {
            "relay_urls": ["http://r1/relay/0", "http://r2/relay/0"],
            "heater_kw": [1.07, 1.06],
            "sensor_name": "Autotalli",
            "sensor_status_file": "data/shelly_ht_status.json",
            "target_c": 5.0,
            "floor_c": 2.0,
            "stale_after_s": 10800,
            "off_timer_s": 4500,
            "loss_k_per_h_per_k": 0.010,
            "loss_k_per_h": 0.08,
            "heat_gross_k_per_h": 0.6,
        }
        config.shelly_em3_url = "http://em3"
        config.influxdb_bucket_load_control = "load_control"
        influx = Mock()
        monkeypatch.delenv("STAGING_MODE", raising=False)
        monkeypatch.setattr(guard, "get_config", lambda: config)
        monkeypatch.setattr(guard, "InfluxClient", lambda c, timeout_ms: influx)
        return influx

    def test_an_unreadable_meter_writes_nothing(self, run, monkeypatch):
        monkeypatch.setattr(
            guard.requests, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert guard.main() == 1
        run.write_with_retry.assert_not_called()

    def test_a_reading_goes_to_the_minute_log_and_a_point_is_written(self, run, data, monkeypatch):
        monkeypatch.setattr(guard.requests, "get", Mock(return_value=_response(STATUS)))
        assert guard.main() == 0
        assert len(json.loads((data / "guard_minutes.json").read_text())) == 1
        point = run.write_with_retry.call_args.kwargs["record"]
        assert point.to_line_protocol().startswith("load_guard,data_type=dry_run")

    def test_a_failed_write_still_ends_well(self, run, monkeypatch):
        monkeypatch.setattr(guard.requests, "get", Mock(return_value=_response(STATUS)))
        run.write_with_retry = Mock(side_effect=RuntimeError("influx down"))
        assert guard.main() == 0

    def test_a_full_disk_does_not_stop_the_guard(self, run, monkeypatch):
        monkeypatch.setattr(guard.requests, "get", Mock(return_value=_response(STATUS)))
        monkeypatch.setattr(guard, "save_minutes", Mock(side_effect=OSError("no space")))
        assert guard.main() == 0
        run.write_with_retry.assert_called_once()

    def test_the_test_limit_replaces_the_cap(self, run, monkeypatch):
        monkeypatch.setattr(guard.requests, "get", Mock(return_value=_response(STATUS)))
        monkeypatch.setenv("LOAD_GUARD_TEST_LIMIT_KW", "1.5")
        assert guard.main() == 0
        line = run.write_with_retry.call_args.kwargs["record"].to_line_protocol()
        assert "limit_kw=1.5" in line

    def test_staging_reads_the_mock_pump_state(self, run, data, monkeypatch):
        monkeypatch.setattr(guard.requests, "get", Mock(return_value=_response(STATUS)))
        monkeypatch.setenv("STAGING_MODE", "true")
        seen = []
        monkeypatch.setattr(guard, "pump_command", lambda path: seen.append(path.name))
        assert guard.main() == 0
        assert seen == ["pump_state_dry_run.json"]


@pytest.mark.parametrize(
    "section",
    [
        {"mode": "act", "pump_ale_kw": 3.0},
        {"mode": "live", "pump_ale_kw": 3.0, "pump_evu_kw": 1.0},
    ],
)
def test_a_bad_guard_section_is_refused(section):
    with pytest.raises(ValueError):
        guard.guard_settings(section, 2.13)


class TestAct:
    NOW = 1_800_000_000.0 - 1_800_000_000.0 % 3600 + 1200

    def _verdict(self, loads):
        sheds = tuple(guard.lg.Shed(name, 1.0) for name in loads)
        return guard.lg.Verdict(
            guard.lg.hour_start(self.NOW), 9.0, 9.0, 7.9, "car_5kw", sheds, True
        )

    def _pump(self, last="ON", ok=True):
        pump = Mock(last_command=last)
        pump.execute_command = Mock(return_value={"success": ok, "error": None if ok else "i2c"})
        return pump

    def test_a_shed_switches_the_load_and_holds_it_to_the_hour_end(self, data, monkeypatch):
        path = data / "load_overrides.json"
        monkeypatch.setattr(guard, "OVERRIDES", path)
        relays = [guard.MockGarageRelay("a", ison=True), guard.MockGarageRelay("b", ison=True)]
        pump = self._pump()
        seen = []

        def command(cmd, scheduled, actual):
            seen.append(guard.lo.active_override(path, "geothermal_pump", self.NOW))
            return {"success": True}

        pump.execute_command = Mock(side_effect=command)
        done = guard.act(
            self._verdict(["garage_off", "pump_ale"]), self.NOW, relays, 4500, lambda: pump
        )
        assert done == ["garage_off", "pump_ale"]
        assert all(r.commands == [(False, 4500)] for r in relays)
        assert seen[0] is not None, "the hold must be written before the pump switches"
        held = guard.lo.read_overrides(path)
        assert {o.load_id for o in held} == {"garage_heater", "geothermal_pump"}
        assert all(o.until == guard.lg.hour_start(self.NOW) + 3600 for o in held)
        assert [o.restore_to for o in held if o.load_id == "geothermal_pump"] == ["ON"]

    def test_evu_wins_over_ale_and_a_pump_already_there_is_left(self, data, monkeypatch):
        monkeypatch.setattr(guard, "OVERRIDES", data / "load_overrides.json")
        pump = self._pump()
        verdict = self._verdict(["pump_ale", "pump_evu"])
        assert guard.act(verdict, self.NOW, [], 4500, lambda: pump) == ["pump_evu"]
        pump.last_command = "EVU"
        assert guard.act(verdict, self.NOW + 60, [], 4500, lambda: pump) == []
        assert pump.execute_command.call_count == 1

    def test_a_failed_pump_command_leaves_no_hold(self, data, monkeypatch):
        monkeypatch.setattr(guard, "OVERRIDES", data / "load_overrides.json")
        pump = self._pump(ok=False)
        assert guard.act(self._verdict(["pump_ale"]), self.NOW, [], 4500, lambda: pump) == []
        assert guard.lo.read_overrides(data / "load_overrides.json") == []

    def test_a_failed_relay_leaves_no_hold(self, data, monkeypatch):
        monkeypatch.setattr(guard, "OVERRIDES", data / "load_overrides.json")
        dead = Mock(set=Mock(return_value=None))
        good = guard.MockGarageRelay("b", ison=True)
        verdict = self._verdict(["garage_off"])
        assert guard.act(verdict, self.NOW, [dead, good], 4500, lambda: Mock()) == []
        assert guard.lo.read_overrides(data / "load_overrides.json") == []

    def test_an_unwritable_hold_switches_nothing(self, data, monkeypatch):
        monkeypatch.setattr(guard, "OVERRIDES", data / "load_overrides.json")
        monkeypatch.setattr(guard.lo, "write_overrides", Mock(side_effect=OSError("read-only")))
        pump = self._pump()
        with pytest.raises(OSError):
            guard.act(self._verdict(["pump_ale"]), self.NOW, [], 4500, lambda: pump)
        pump.execute_command.assert_not_called()

    def test_a_busy_lock_sheds_nothing(self, data, monkeypatch):
        monkeypatch.setattr(guard, "OVERRIDES", data / "load_overrides.json")
        pump = self._pump()
        with guard.control_lock(0):
            with pytest.raises(guard.ControlLockTimeout):
                guard.act(self._verdict(["pump_ale"]), self.NOW, [], 4500, lambda: pump)
        pump.execute_command.assert_not_called()
