"""The EVU release log: one point per release of EVU, from every source,
written after the command and the state save, with a one-try 3 s client."""

import json
import os
import time
import types
from unittest.mock import MagicMock

import pytest

from src.control import evu_release
from src.control.hardware_implementations import MockHardwareInterface
from src.control.pump_controller import PumpController


def _pump(tmp_path, last_command=None, source="executor"):
    pump = PumpController(
        hardware=MockHardwareInterface(), state_file=str(tmp_path / "s.json"), source=source
    )
    pump.last_command = last_command
    return pump


class TestEverySource:
    def test_an_executor_command_out_of_evu(self, tmp_path, evu_releases):
        assert _pump(tmp_path, "EVU").execute_command("ALE", 100, 100)["success"]
        assert evu_releases == [("executor", "ALE", "EVU", "command")]

    def test_the_periodic_pulse(self, tmp_path, evu_releases):
        assert _pump(tmp_path, "ON").perform_evu_cycle(100)["success"]
        assert evu_releases == [("pulse", "ON", "ON", "pulse")]

    def test_the_ale_to_on_pulse(self, tmp_path, evu_releases):
        assert _pump(tmp_path, "ALE").execute_command("ON", 100, 100)["success"]
        assert evu_releases == [("executor", "ON", "ALE", "ale_on_pulse")]

    def test_a_manual_command(self, tmp_path, evu_releases):
        assert _pump(tmp_path, "EVU", "manual").execute_command("ON", 100, 100)["success"]
        assert evu_releases == [("manual", "ON", "EVU", "command")]

    def test_the_boot_restore(self, tmp_path, evu_releases):
        assert _pump(tmp_path, "EVU").apply_state("ALE", 100)["success"]
        assert evu_releases == [("boot", "ALE", "EVU", "boot_pulse")]

    def test_the_pre_pulse(self, tmp_path, evu_releases):
        assert _pump(tmp_path, "ALE").pre_pulse(100)["success"]
        assert evu_releases == [("pulse", "ALE", "ALE", "pre_pulse")]


class TestTheReleaseIsKept:
    """Each release is saved with the pump state, where every caller reads it."""

    @pytest.mark.parametrize(
        "before,act,released",
        [
            ("EVU", lambda p: p.execute_command("ALE", 100, 100), 100),
            ("ON", lambda p: p.perform_evu_cycle(100), 130),
            ("ALE", lambda p: p.execute_command("ON", 100, 100), 130),
            ("EVU", lambda p: p.apply_state("ALE", 100), 130),
            ("ALE", lambda p: p.pre_pulse(100), 130),
        ],
        ids=["command", "pulse", "ale_on_pulse", "boot", "pre_pulse"],
    )
    def test_every_release_is_saved(self, tmp_path, before, act, released):
        assert act(_pump(tmp_path, before))["success"]
        assert json.loads((tmp_path / "s.json").read_text())["last_release_time"] == released

    def test_no_release_keeps_the_old_time(self, tmp_path):
        pump = _pump(tmp_path, "ON")
        pump.last_release_time = 50
        assert pump.execute_command("EVU", 100, 100)["success"]
        assert json.loads((tmp_path / "s.json").read_text())["last_release_time"] == 50


class TestNoRelease:
    @pytest.mark.parametrize(
        "before,command", [("EVU", "EVU"), ("ON", "EVU"), ("ALE", "EVU"), ("ON", "ALE")]
    )
    def test_no_point_when_evu_is_not_left(self, tmp_path, evu_releases, before, command):
        assert _pump(tmp_path, before).execute_command(command, 100, 100)["success"]
        assert evu_releases == []

    def test_a_repeated_on_is_no_release(self, tmp_path, evu_releases):
        pump = _pump(tmp_path, "ON")
        pump.on_since = 90
        assert pump.execute_command("ON", 100, 100)["success"]
        assert evu_releases == []

    def test_a_boot_evu_is_no_release(self, tmp_path, evu_releases):
        assert _pump(tmp_path, "ON").apply_state("EVU", 100)["success"]
        assert evu_releases == []

    def test_a_failed_return_to_on_stays_in_evu(self, tmp_path, evu_releases):
        pump = _pump(tmp_path, "ON")
        results = iter([True, False, False, False])
        pump.hardware.write_pump_command = lambda c: next(results)
        assert not pump.perform_evu_cycle(100)["success"]
        assert evu_releases == []

    def test_a_failed_command_after_the_pulse_stays_in_evu(self, tmp_path, evu_releases):
        pump = _pump(tmp_path, "ALE")
        results = iter([True, False, False, False])
        pump.hardware.write_pump_command = lambda c: next(results)
        assert not pump.execute_command("ON", 100, 100)["success"]
        assert evu_releases == []


class TestOrder:
    def test_the_point_comes_after_the_state_save(self, tmp_path, monkeypatch):
        pump = _pump(tmp_path, "EVU")
        seen = []

        def record(source, new_state, previous, via, now, mock):
            seen.append(json.loads((tmp_path / "s.json").read_text())["last_command"])
            seen.append(list(pump.hardware.commands_executed))

        monkeypatch.setattr(
            "src.control.pump_controller.evu_release", types.SimpleNamespace(log_release=record)
        )
        pump.execute_command("ON", 100, 100)
        assert seen == ["ON", ["ON"]]

    def test_the_pulse_point_comes_after_the_return_to_on(self, tmp_path, monkeypatch):
        pump = _pump(tmp_path, "ON")
        seen = []

        def record(*args):
            seen.append(list(pump.hardware.commands_executed))

        monkeypatch.setattr(
            "src.control.pump_controller.evu_release", types.SimpleNamespace(log_release=record)
        )
        pump.perform_evu_cycle(100)
        assert seen == [["EVU", "ON"]]


class _Config:
    influxdb_org = "org"

    def __init__(self, bucket):
        self.influxdb_bucket_load_control = bucket


@pytest.fixture
def client(monkeypatch):
    made = []

    def make(config, timeout_ms):
        c = MagicMock()
        c.timeout_ms = timeout_ms
        made.append(c)
        return c

    monkeypatch.setattr(evu_release, "InfluxClient", make)
    monkeypatch.delenv("STAGING_MODE", raising=False)
    return made


class TestWriter:
    def test_one_try_with_a_3_s_client(self, monkeypatch, client):
        monkeypatch.setattr(evu_release, "get_config", lambda: _Config("load_control"))
        assert evu_release.log_release("executor", "ON", "EVU", "command", 100, False)
        assert client[0].timeout_ms == 3000
        assert client[0].write_api.write.call_count == 1
        assert client[0].write_api.write.call_args[1]["bucket"] == "load_control"

    def test_a_failed_write_is_dropped(self, monkeypatch, client):
        monkeypatch.setattr(evu_release, "get_config", lambda: _Config("load_control"))

        def make(config, timeout_ms):
            c = MagicMock()
            c.write_api.write.side_effect = TimeoutError("hung")
            return c

        monkeypatch.setattr(evu_release, "InfluxClient", make)
        assert not evu_release.log_release("pulse", "ON", "ON", "pulse", 100, False)

    def test_a_missing_config_is_dropped(self, monkeypatch, client):
        def broken():
            raise ValueError("no env")

        monkeypatch.setattr(evu_release, "get_config", broken)
        assert not evu_release.log_release("boot", "ON", "EVU", "boot_pulse", 100, False)

    def test_staging_mock_pump_writes_to_the_staging_bucket(self, monkeypatch, client):
        monkeypatch.setattr(evu_release, "get_config", lambda: _Config("load_control_staging"))
        assert evu_release.log_release("executor", "ON", "EVU", "command", 100, True)
        assert client[0].write_api.write.call_args[1]["bucket"] == "load_control_staging"

    def test_a_mock_pump_in_production_writes_nothing(self, monkeypatch, client):
        monkeypatch.setattr(evu_release, "get_config", lambda: _Config("load_control"))
        assert not evu_release.log_release("manual", "ON", "EVU", "command", 100, True)
        assert client == []

    def test_an_unknown_source_is_a_bug(self):
        with pytest.raises(ValueError):
            evu_release.log_release("someone", "ON", "EVU", "command", 100, False)

    def test_the_point_carries_source_and_states(self):
        line = evu_release.build_point("pulse", "ON", "ON", "pulse", 60).to_line_protocol()
        assert line.startswith("evu_release,source=pulse ")
        assert 'new_state="ON"' in line and 'via="pulse"' in line


class TestTiming:
    def test_a_hung_write_adds_only_its_own_timeout(self, tmp_path, monkeypatch):
        """The command is done before the write starts; the write is bounded."""
        os.environ["TEST_MODE"] = "true"
        monkeypatch.setattr(evu_release, "get_config", lambda: _Config("load_control"))

        def make(config, timeout_ms):
            c = MagicMock()
            c.write_api.write.side_effect = lambda **kw: time.sleep(timeout_ms / 1000 / 30)
            return c

        monkeypatch.setattr(evu_release, "InfluxClient", make)
        monkeypatch.setattr("src.control.pump_controller.evu_release", evu_release)
        pump = PumpController(hardware=MockHardwareInterface(), state_file=str(tmp_path / "s"))
        pump.hardware = type("Real", (), {"write_pump_command": lambda self, c: True})()
        pump.last_command = "EVU"
        start = time.monotonic()
        assert pump.execute_command("ON", 100, 100)["success"]
        assert time.monotonic() - start < 3.0
