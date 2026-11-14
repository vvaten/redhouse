"""Tests for the month's power charge base and its hourly update."""

import datetime
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.common.local_time import HELSINKI
from src.control import power_cap as pc

SETTINGS = pc.CapSettings(
    night_factor=0.8,
    night_start_hour=22,
    night_end_hour=7,
    month_floor_kw=7.5,
    plan_margin_kw=0.3,
    guard_margin_kw=0.1,
    tariff_eur_per_kw=1.97,
    max_import_kw=34.5,
)
SECTION = {
    "night_factor": 0.8,
    "night_start_hour": 22,
    "night_end_hour": 7,
    "month_floor_kw": 7.5,
    "plan_margin_kw": 0.3,
    "guard_margin_kw": 0.1,
    "tariff_eur_per_kw": 1.97,
    "max_import_kw": 34.5,
}
UTC = datetime.timezone.utc
H = datetime.timedelta(hours=1)


def _local(y, mo, d, h):
    return HELSINKI.localize(datetime.datetime(y, mo, d, h)).astimezone(UTC)


class TestFactor:
    @pytest.mark.parametrize("hour, expected", [(21, 1.0), (22, 0.8), (0, 0.8), (6, 0.8), (7, 1.0)])
    def test_night_hours_count_at_80_percent(self, hour, expected):
        assert pc.factor(_local(2026, 11, 10, hour), SETTINGS) == expected

    def test_the_local_hour_decides_across_the_clock_change(self):
        # 2026-10-25 04:00 UTC is 06:00 local after the change: still night
        assert pc.factor(datetime.datetime(2026, 10, 25, 4, tzinfo=UTC), SETTINGS) == 0.8
        assert pc.factor(datetime.datetime(2026, 10, 25, 5, tzinfo=UTC), SETTINGS) == 1.0


class TestHourlyImport:
    t0 = datetime.datetime(2026, 11, 1, tzinfo=UTC)

    def test_an_hour_is_the_next_hours_first_value_minus_its_own(self):
        counter = {self.t0: 1000.0, self.t0 + H: 4500.0}
        assert pc.hourly_import_kw(counter, SETTINGS) == {self.t0: 3.5}

    def test_a_net_export_hour_imports_nothing(self):
        counter = {self.t0: 5000.0, self.t0 + H: 4000.0}
        assert pc.hourly_import_kw(counter, SETTINGS) == {self.t0: 0.0}

    def test_a_reset_a_glitch_or_a_gap_gives_no_value(self):
        counter = {
            self.t0: 9_000_000.0,
            self.t0 + H: 100.0,
            self.t0 + 2 * H: 99_000_000.0,
            self.t0 + 4 * H: 99_001_000.0,
        }
        assert pc.hourly_import_kw(counter, SETTINGS) == {}


class TestMonthCap:
    def test_the_third_highest_counted_hour(self):
        t = [_local(2026, 11, 2, h) for h in (8, 9, 10, 23)]
        counted = pc.counted_hours({t[0]: 9.0, t[1]: 8.0, t[2]: 7.9, t[3]: 12.0}, SETTINGS)
        cap, top = pc.month_cap(counted, SETTINGS)
        assert top[0] == (t[3], pytest.approx(9.6))
        assert cap == pytest.approx(8.0)

    def test_the_floor_holds_early_in_the_month(self):
        cap, top = pc.month_cap({_local(2026, 11, 1, 8): 9.0}, SETTINGS)
        assert cap == 7.5 and len(top) == 1

    def test_the_limits_keep_their_margins(self):
        assert pc.plan_limit_kw(8.0, SETTINGS) == pytest.approx(7.7)
        assert pc.guard_limit_kw(8.0, SETTINGS) == pytest.approx(7.9)


class TestSettings:
    def test_a_missing_key_is_refused(self):
        with pytest.raises(ValueError, match="night_factor"):
            pc.load_cap_settings({"month_floor_kw": 7.5})

    def test_a_fractional_hour_is_refused(self):
        with pytest.raises(ValueError, match="whole hour"):
            pc.load_cap_settings({**SECTION, "night_start_hour": 21.5})


class TestState:
    def test_a_written_state_is_read_back(self, tmp_path):
        path = tmp_path / "power_cap_state.json"
        hour = _local(2026, 11, 2, 8)
        pc.write_state(path, pc.state_dict("2026-11", 7.92, [], {hour: 9.0}, 0.0))
        assert pc.read_cap_kw(path, _local(2026, 11, 14, 20), SETTINGS) == pytest.approx(7.92)
        assert pc.stored_counted(pc.read_state(path), "2026-11") == {hour: 9.0}
        assert list(tmp_path.iterdir()) == [path]

    def test_last_months_state_gives_the_floor(self, tmp_path):
        path = tmp_path / "power_cap_state.json"
        path.write_text(json.dumps({"month": "2026-10", "cap_kw": 9.0}))
        assert pc.read_cap_kw(path, _local(2026, 11, 1, 1), SETTINGS) == 7.5

    @pytest.mark.parametrize(
        "text",
        [
            "{trunc",
            "[]",
            '{"month": "2026-11", "cap_kw": null}',
            '{"month": "2026-11", "cap_kw": NaN}',
        ],
    )
    def test_a_broken_state_gives_the_floor(self, tmp_path, text):
        path = tmp_path / "s.json"
        path.write_text(text)
        assert pc.read_cap_kw(path, _local(2026, 11, 2, 1), SETTINGS) == 7.5

    def test_only_completed_hours_of_the_month_count(self):
        now = _local(2026, 11, 14, 20) + datetime.timedelta(minutes=17)
        hours = {
            _local(2026, 10, 31, 23): 1.0,
            _local(2026, 11, 1, 0): 2.0,
            _local(2026, 11, 14, 19): 3.0,
            _local(2026, 11, 14, 20): 4.0,
        }
        assert set(pc.in_month(hours, "2026-11", now).values()) == {2.0, 3.0}

    def test_missing_hours_are_listed(self):
        now = _local(2026, 11, 1, 3) + datetime.timedelta(minutes=17)
        counted = {_local(2026, 11, 1, 0): 1.0, _local(2026, 11, 1, 2): 1.0}
        assert pc.missing_hours(counted, now) == [_local(2026, 11, 1, 1)]


def _wrapper():
    path = Path(__file__).resolve().parents[2] / "update_power_cap.py"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record(t, value):
    record = Mock()
    record.get_time = Mock(return_value=t)
    record.get_value = Mock(return_value=value)
    return record


class TestUpdate:
    @pytest.fixture
    def run(self, monkeypatch, tmp_path):
        module = _wrapper()
        config = Mock(power_cap_section=SECTION)
        config.influxdb_bucket_shelly_em3_raw = "shelly_em3_emeters_raw"
        config.influxdb_bucket_load_control = "load_control"
        influx = Mock()
        monkeypatch.setattr(module, "get_config", lambda: config)
        monkeypatch.setattr(module, "InfluxClient", lambda c: influx)
        monkeypatch.setattr(module, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr(module.ConfigValidator, "validate_write", Mock(return_value=None))
        monkeypatch.setattr(sys, "argv", ["x"])

        def at(now, first):
            fake_now = Mock(wraps=datetime.datetime)
            fake_now.now = Mock(return_value=now)
            monkeypatch.setattr(module.datetime, "datetime", fake_now)
            influx.query_with_retry = Mock(
                return_value=[Mock(records=[_record(t, v) for t, v in sorted(first.items())])]
            )
            assert module.main() == 0
            return pc.read_state(module.STATE_FILE), influx

        return at

    def test_a_load_switched_at_the_hour_edge_lands_in_its_hour(self, run):
        start = _local(2026, 11, 2, 9)
        # 1 kW base, then 9 kW from 10:00: the first value of each hour is at :00
        first = {start - H: 0.0, start: 1000.0, start + H: 10000.0, start + 2 * H: 19000.0}
        state, _ = run(start + 2 * H + datetime.timedelta(minutes=17), first)
        assert state["counted_kw"][start.isoformat()] == pytest.approx(9.0)

    def test_an_empty_query_keeps_the_months_cap(self, run, tmp_path):
        hour = _local(2026, 11, 2, 9)
        first = {hour + k * H: 9000.0 * k for k in range(5)}
        state, _ = run(hour + 5 * H, first)
        assert state["cap_kw"] == pytest.approx(9.0)
        state, _ = run(hour + 6 * H, {})
        assert state["cap_kw"] == pytest.approx(9.0)

    def test_the_last_hour_of_a_month_is_written_after_midnight(self, run):
        last = _local(2026, 11, 30, 23)
        first = {last: 0.0, last + H: 12000.0}
        state, influx = run(last + H + datetime.timedelta(minutes=17), first)
        assert state["month"] == "2026-12"
        lines = [p.to_line_protocol() for p in influx.write_with_retry.call_args.kwargs["record"]]
        assert any("month=2026-11" in line and "kind=hour" in line for line in lines)
