"""Tests for the garage heater controller and its relay class."""

import contextlib
import datetime
import importlib.util
import json
import sys
from unittest.mock import Mock, patch

import pytest
import requests

from src.control import garage_controller as gc
from src.control.garage_model import SLOT_S
from src.control.hardware_implementations import GarageRelay, MockGarageRelay

SECTION = {
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
NOW = 1_800_000_300  # 5 min into a slot
START = NOW - NOW % SLOT_S


@pytest.fixture(autouse=True)
def _no_env_overrides(monkeypatch):
    for env in gc.ENV_OVERRIDES.values():
        monkeypatch.delenv(env, raising=False)


def _settings(**changes):
    return gc.load_settings({**SECTION, **changes})


def _series(value, n=100, start=START - 3600):
    return {start + i * SLOT_S: value for i in range(n)}


class TestSettings:
    def test_a_missing_key_is_an_error(self):
        section = dict(SECTION)
        del section["off_timer_s"]
        with pytest.raises(ValueError, match="off_timer_s"):
            gc.load_settings(section)

    def test_the_test_run_overrides_come_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("GARAGE_TARGET_C", "12")
        monkeypatch.setenv("GARAGE_FLOOR_C", "9.5")
        settings = _settings()
        assert settings.params.target_c == 12.0
        assert settings.params.floor_c == 9.5

    def test_a_floor_over_the_target_is_refused(self, monkeypatch):
        monkeypatch.setenv("GARAGE_FLOOR_C", "6")
        with pytest.raises(ValueError):
            _settings()

    def test_a_relative_status_file_is_under_the_install(self):
        assert _settings().status_file == gc.REPO / "data" / "shelly_ht_status.json"

    @pytest.mark.parametrize("timer", [0, 1800, 4501])
    def test_an_unsafe_off_timer_is_refused(self, timer):
        with pytest.raises(ValueError, match="off_timer_s"):
            _settings(off_timer_s=timer)

    def test_the_heater_power_adds_up(self):
        assert _settings().total_kw == pytest.approx(2.13)


class TestReading:
    def _file(self, tmp_path, states):
        path = tmp_path / "status.json"
        path.write_text(json.dumps(states))
        return path

    def test_the_named_sensor_is_found(self, tmp_path):
        path = self._file(tmp_path, {"shellyht-1-180": {"temp": 6.4, "updated": 100.0}})
        name_of = {"shellyht-1-180": "Autotalli"}.get
        assert gc.read_reading(path, "Autotalli", name_of) == (6.4, 100.0)

    def test_no_file_or_no_sensor_gives_none(self, tmp_path):
        name_of = {"x": "Other"}.get
        assert gc.read_reading(tmp_path / "none.json", "Autotalli", name_of) == (None, None)
        path = self._file(tmp_path, {"x": {"temp": 6.4, "updated": 100.0}})
        assert gc.read_reading(path, "Autotalli", name_of) == (None, None)

    def test_the_newest_of_two_matching_ids_wins(self, tmp_path):
        path = self._file(
            tmp_path,
            {"old-180": {"temp": 1.0, "updated": 50.0}, "new-180": {"temp": 6.0, "updated": 900.0}},
        )
        name_of = {"old-180": "Autotalli", "new-180": "Autotalli"}.get
        assert gc.read_reading(path, "Autotalli", name_of) == (6.0, 900.0)

    def test_a_null_temperature_gives_none(self, tmp_path):
        path = self._file(tmp_path, {"a": {"temp": None, "updated": 100.0}})
        assert gc.read_reading(path, "Autotalli", {"a": "Autotalli"}.get) == (None, None)


class TestSlots:
    def test_slots_start_at_the_current_slot(self):
        slots = gc.build_slots(START, _series(0.1), _series(-5.0))
        assert slots[0].start == START
        assert len(slots) == gc.HORIZON_SLOTS

    def test_a_forecast_gap_carries_the_last_value_on(self):
        outdoor = {START - SLOT_S: -7.0, START + 4 * SLOT_S: -9.0}
        slots = gc.build_slots(START, {}, outdoor)
        assert [s.t_out_c for s in slots[:6]] == [-7.0] * 4 + [-9.0] * 2

    def test_no_forecast_gives_no_slots(self):
        assert gc.build_slots(START, _series(0.1), {}) == []

    def test_prices_past_the_known_ones_are_none(self):
        slots = gc.build_slots(START, {START: 0.1}, _series(-5.0))
        assert slots[0].price == 0.1
        assert slots[1].price is None


class TestTick:
    def test_cold_weather_heats(self):
        decision, garage_plan = gc.tick(
            NOW, _settings(), (2.1, NOW - 60), _series(0.1), _series(-20.0)
        )
        assert garage_plan is not None
        assert decision.on

    def test_no_forecast_still_heats_under_the_floor(self):
        decision, garage_plan = gc.tick(NOW, _settings(), (1.5, NOW - 60), {}, {})
        assert garage_plan is None
        assert decision.on and decision.reason == "under_floor"

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), 999.0])
    def test_an_implausible_reading_heats(self, bad):
        decision, garage_plan = gc.tick(
            NOW, _settings(), (bad, NOW - 60), _series(0.1), _series(-5.0)
        )
        assert garage_plan is None
        assert decision.on and decision.reason == "stale_reading"

    def test_a_reading_from_the_future_heats(self):
        decision, _ = gc.tick(NOW, _settings(), (4.0, NOW + 3600), _series(0.1), _series(-5.0))
        assert decision.on and decision.reason == "stale_reading"

    def test_a_guard_hold_keeps_the_heat_off_above_the_floor(self):
        decision, _ = gc.tick(
            NOW, _settings(), (2.5, NOW - 60), _series(0.1), _series(-20.0), override_off=True
        )
        assert not decision.on and decision.reason == "overridden_by_guard"
        under, _ = gc.tick(
            NOW, _settings(), (1.5, NOW - 60), _series(0.1), _series(-20.0), override_off=True
        )
        assert under.on

    def test_no_reading_heats(self):
        decision, _ = gc.tick(NOW, _settings(), (None, None), _series(0.1), _series(-5.0))
        assert decision.on and decision.reason == "stale_reading"


class TestApply:
    def test_every_relay_gets_the_command_and_off_carries_the_timer(self):
        relays = [MockGarageRelay("a", ison=True), MockGarageRelay("b", ison=True)]
        states = gc.apply(gc.Decision(False, "at_target"), relays, 4500)
        assert states == (False, False)
        assert all(r.commands == [(False, 4500)] for r in relays)

    def test_a_failed_relay_does_not_stop_the_other(self):
        dead = Mock()
        dead.url = "dead"
        dead.set = Mock(return_value=None)
        good = MockGarageRelay("good")
        states = gc.apply(gc.Decision(True, "plan_heat"), [dead, good], 4500)
        assert states == (None, True)


class TestRunOnce:
    def _io(self, reading=(6.0, NOW - 60), locked=None):
        written = []

        @contextlib.contextmanager
        def lock():
            if locked is not None:
                locked.append(True)
            yield

        io = gc.GarageIO(
            read_reading=lambda s: reading,
            fetch_inputs=lambda start: (_series(0.1), _series(5.0)),
            lock=lock,
            write=written.append,
        )
        return io, written

    def test_the_relays_switch_under_the_lock_and_a_point_is_written(self):
        inside = []

        @contextlib.contextmanager
        def lock():
            inside.append(True)
            yield
            inside.append(False)

        io, written = self._io()
        io.lock = lock

        class Watched(MockGarageRelay):
            def set(self, on, off_timer_s):
                assert inside == [True], "a relay switched outside the lock"
                return super().set(on, off_timer_s)

        relays = [Watched("a", ison=True), Watched("b", ison=True)]
        result = gc.run_once(NOW, _settings(), relays, io)
        assert inside == [True, False]
        assert result.success and not result.decision.on
        line = written[0].to_line_protocol()
        assert "load_id=garage_heater" in line
        assert "relay1_on=0i" in line and "relay2_on=0i" in line
        assert 'reason="at_target"' in line

    def test_the_power_counts_only_relays_that_are_on(self):
        settings = _settings()
        result = gc.TickResult(gc.Decision(True, "x"), 1.0, 1.0, None, (True, None), False)
        point = gc.build_point(NOW, settings, result)
        assert point._fields["power_kw"] == pytest.approx(1.07)
        assert point._fields["relay2_on"] == -1


class TestGarageRelay:
    def _response(self, ison):
        response = Mock()
        response.raise_for_status = Mock()
        response.json = Mock(return_value={"ison": ison, "has_timer": not ison})
        return response

    def test_off_carries_the_flip_back_timer(self):
        with patch("src.control.hardware_implementations.requests.get") as get:
            get.return_value = self._response(False)
            assert GarageRelay("http://r/relay/0").set(False, 4500) is False
        assert get.call_args.args[0] == "http://r/relay/0?turn=off&timer=4500"

    def test_on_has_no_timer(self):
        with patch("src.control.hardware_implementations.requests.get") as get:
            get.return_value = self._response(True)
            assert GarageRelay("http://r/relay/0").set(True, 4500) is True
        assert get.call_args.args[0] == "http://r/relay/0?turn=on"

    def test_a_slow_relay_is_tried_again(self):
        with patch("src.control.hardware_implementations.requests.get") as get:
            get.side_effect = [requests.Timeout("slow"), self._response(True)]
            relay = GarageRelay("http://r/relay/0", sleep=lambda s: None)
            assert relay.set(True, 4500) is True
        assert get.call_count == 2

    def test_an_off_without_the_flip_back_timer_is_a_failure(self):
        response = self._response(False)
        response.json = Mock(return_value={"ison": False, "has_timer": False})
        with patch("src.control.hardware_implementations.requests.get", return_value=response):
            assert GarageRelay("http://r/relay/0").set(False, 4500) is None

    def test_a_reply_that_is_not_a_status_is_tried_again(self):
        bad = Mock(raise_for_status=Mock(), json=Mock(return_value=["x"]))
        with patch("src.control.hardware_implementations.requests.get") as get:
            get.side_effect = [bad, self._response(True)]
            assert GarageRelay("http://r/relay/0", sleep=lambda s: None).set(True, 4500) is True

    def test_a_dead_relay_gives_none_after_the_tries(self):
        with patch("src.control.hardware_implementations.requests.get") as get:
            get.side_effect = requests.ConnectionError("down")
            relay = GarageRelay("http://r/relay/0", sleep=lambda s: None)
            assert relay.set(True, 4500) is None
        assert get.call_count == GarageRelay.TRIES


class TestQueries:
    def _record(self, epoch, value):
        record = Mock()
        record.get_time = Mock(
            return_value=datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
        )
        record.get_value = Mock(return_value=value)
        return record

    def test_the_series_is_keyed_by_slot_epoch(self):
        records = [self._record(START, 0.12), self._record(START + 900, None)]
        influx = Mock()
        influx.query_with_retry = Mock(return_value=[Mock(records=records)])
        series = gc._query_series(influx, "spotprice", "spot", "price_total", START, 25)
        assert series == {START: 0.12}
        flux = influx.query_with_retry.call_args.args[0]
        assert 'r._measurement == "spot" and r._field == "price_total"' in flux
        assert 'timeSrc: "_start"' in flux

    def test_inputs_read_prices_and_the_outdoor_forecast(self):
        influx = Mock()
        influx.query_with_retry = Mock(return_value=[])
        config = Mock(influxdb_bucket_spotprice="spotprice", influxdb_bucket_weather="weather")
        assert gc.fetch_inputs(influx, config, START) == ({}, {})
        fluxes = [c.args[0] for c in influx.query_with_retry.call_args_list]
        assert any('"spot"' in f and "price_total" in f for f in fluxes)
        assert any('"weather"' in f and '"Air temperature"' in f for f in fluxes)

    def test_a_failed_query_gives_empty_inputs(self):
        influx = Mock()
        influx.query_with_retry = Mock(side_effect=RuntimeError("down"))
        config = Mock(influxdb_bucket_spotprice="spotprice", influxdb_bucket_weather="weather")
        assert gc.fetch_inputs(influx, config, START) == ({}, {})


def _wrapper():
    path = gc.REPO / "run_garage_heater.py"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestWrapper:
    @pytest.fixture
    def wrapper(self, monkeypatch, tmp_path):
        module = _wrapper()
        config = Mock()
        config.garage_section = SECTION
        config.influxdb_bucket_load_control = "load_control"
        config.get = Mock(return_value=None)
        monkeypatch.setattr(module, "get_config", lambda: config)
        monkeypatch.setattr(module, "InfluxClient", Mock())
        monkeypatch.setattr(module, "ALERT_STATE", tmp_path / "alerts.json")
        runs = []

        def run_once(now, settings, relays, io):
            runs.append([type(r).__name__ for r in relays])
            decision = gc.Decision(False, "at_target")
            return gc.TickResult(decision, 6.0, 60.0, None, (False, False), True)

        monkeypatch.setattr(module.gc, "run_once", run_once)
        monkeypatch.delenv("STAGING_MODE", raising=False)
        return module, config, runs

    def test_production_drives_the_real_relays(self, wrapper, monkeypatch):
        module, config, runs = wrapper
        monkeypatch.setattr(sys, "argv", ["x"])
        assert module.main() == 0
        assert runs == [["GarageRelay", "GarageRelay"]]

    @pytest.mark.parametrize("how", ["flag", "bucket", "dry_run"])
    def test_staging_or_a_dry_run_uses_mocks(self, wrapper, monkeypatch, how):
        module, config, runs = wrapper
        monkeypatch.setattr(sys, "argv", ["x", "--dry-run"] if how == "dry_run" else ["x"])
        if how == "flag":
            monkeypatch.setenv("STAGING_MODE", "true")
        if how == "bucket":
            config.influxdb_bucket_load_control = "load_control_staging"
        assert module.main() == 0
        assert runs == [["MockGarageRelay", "MockGarageRelay"]]

    def test_staging_mails_nothing(self, wrapper, monkeypatch):
        module, config, runs = wrapper
        config.influxdb_bucket_load_control = "load_control_staging"
        sent = Mock(return_value=True)
        monkeypatch.setattr(module, "send_alert_email", sent)
        module._alert(config, "stale reading", "x")
        sent.assert_not_called()

    def test_an_alert_repeats_only_after_six_hours(self, wrapper, monkeypatch):
        module, config, runs = wrapper
        settings = {"RESEND_API_KEY": "k", "ALERT_EMAIL_TO": "t", "ALERT_EMAIL_FROM": None}
        config.get = Mock(side_effect=lambda key: settings[key])
        sent = Mock(return_value=True)
        monkeypatch.setattr(module, "send_alert_email", sent)
        module._alert(config, "relay failure", "x")
        module._alert(config, "relay failure", "x")
        assert sent.call_count == 1

    def test_a_taken_lock_leaves_the_relays_alone(self, wrapper, monkeypatch):
        module, config, runs = wrapper

        def locked(*args):
            raise module.ControlLockTimeout("held")

        monkeypatch.setattr(module.gc, "run_once", locked)
        monkeypatch.setattr(sys, "argv", ["x"])
        assert module.main() == 1


def test_a_shed_that_lands_while_the_tick_waits_for_the_lock_wins():
    """The guard can shed between the tick's decision and its relay commands."""
    held = []

    @contextlib.contextmanager
    def lock():
        held.append(True)
        yield

    io = gc.GarageIO(
        read_reading=lambda s: (2.5, NOW - 60),
        fetch_inputs=lambda start: (_series(0.01), _series(-25.0)),
        lock=lock,
        write=lambda point: None,
        override_off=lambda now: bool(held),
    )
    relays = [MockGarageRelay("a"), MockGarageRelay("b")]
    result = gc.run_once(NOW, _settings(), relays, io)
    assert result.decision.reason == "overridden_by_guard"
    assert all(r.commands == [(False, 4500)] for r in relays)
