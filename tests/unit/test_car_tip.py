"""Tests for the evening car charging tip."""

import datetime
import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.common.local_time import HELSINKI
from src.control import car_tip as ct
from src.control.power_cap import CapSettings

CAP = CapSettings(0.8, 22, 7, 7.5, 0.3, 0.1, 1.97, 34.5)
SETTINGS = ct.TipSettings(
    session_kwh=45.0,
    kw_low=5.5,
    kw_high=11.2,
    deadline_hour=7,
    naive_start_hour=22,
    min_saving_eur=1.0,
)
UTC = datetime.timezone.utc


def _local(y, mo, d, h):
    return HELSINKI.localize(datetime.datetime(y, mo, d, h)).astimezone(UTC)


def _hours(start, n=26):
    return [start + datetime.timedelta(hours=i) for i in range(n)]


def _prices(cheap=(), cheap_price=0.02, dear=0.15, start=None):
    start = start or _local(2026, 11, 14, 16)
    return {t: cheap_price if t in cheap else dear for t in _hours(start)}


def _ctx(other=3.1, month=None, start=None):
    start = start or _local(2026, 11, 14, 16)
    return {"other_kw": {t: other for t in _hours(start)}, "month_counted": month or {}}


NOW = _local(2026, 11, 14, 16) + datetime.timedelta(minutes=35)
# Cheap from 17:00 to 02:00: a 22:00 start misses most of it
EVENING = {_local(2026, 11, 14, h) for h in range(17, 24)} | {
    _local(2026, 11, 15, h) for h in range(0, 2)
}


def test_a_session_splits_into_full_hours_and_a_rest():
    assert ct._hour_loads(5.5, 45.0) == [5.5] * 8 + [pytest.approx(1.0)]


def test_every_start_ends_by_the_deadline():
    starts = ct.candidates(NOW, 5.5, SETTINGS)
    assert starts[0] == _local(2026, 11, 14, 17)
    assert starts[-1] == _local(2026, 11, 14, 22)


@pytest.mark.parametrize(
    "now, count",
    [
        # Autumn: the night from the 24th to the 25th has 25 hours
        (_local(2026, 10, 24, 16) + datetime.timedelta(minutes=35), 7),
        # Spring: the night from the 28th to the 29th has 23 hours
        (_local(2026, 3, 28, 16) + datetime.timedelta(minutes=35), 5),
    ],
)
def test_the_clock_change_nights_have_the_right_starts(now, count):
    assert len(ct.candidates(now, 5.5, SETTINGS)) == count


def test_a_run_after_midnight_has_no_start_that_fits():
    assert ct.candidates(_local(2026, 11, 15, 1), 5.5, SETTINGS) == []


def test_a_late_catch_up_run_sends_no_tip():
    """At 10:30 the habit's 22:00 hours are tonight; without tomorrow's prices no tip."""
    now = _local(2026, 11, 14, 10) + datetime.timedelta(minutes=30)
    start = _local(2026, 11, 14, 10)
    prices = {t: 0.10 for t in _hours(start, 13)}
    result, reason = ct.tip(prices, now, SETTINGS, CAP, _ctx(start=start))
    assert result is None and "missing" in reason


# A month whose base already sits above any car hour, so nothing rises
HIGH_BASE = {_local(2026, 11, 2, h): 9.0 for h in (8, 9, 10)}


def test_the_cheapest_window_wins():
    result, _ = ct.tip(_prices(EVENING), NOW, SETTINGS, CAP, _ctx(month=HIGH_BASE))
    low, habit, high = result
    assert low.start == _local(2026, 11, 14, 17)
    assert habit.total_eur - low.total_eur >= 1.0
    assert high is not None and high.kw == 11.2


def test_no_tip_when_the_habit_is_good_enough():
    result, reason = ct.tip(_prices(), NOW, SETTINGS, CAP, _ctx())
    assert result is None and "threshold" in reason


def test_a_window_that_raises_the_base_moves_to_the_night():
    """At the floor, day hours with the car count 8.6 kW; night hours 6.9 kW."""
    result, _ = ct.tip(_prices(EVENING), NOW, SETTINGS, CAP, _ctx())
    low = result[0]
    assert low.charge_rise_eur == 0.0
    assert low.start.astimezone(HELSINKI).hour >= 20


def test_a_rise_in_the_power_charge_counts_against_the_window():
    """Cheap day hours raise the base: the energy saving is not worth it."""
    month = {_local(2026, 11, 2, h): 7.6 for h in (8, 9, 10)}
    cheap = {_local(2026, 11, 14, h) for h in range(17, 22)}
    prices = _prices(cheap, cheap_price=0.10, dear=0.15)
    result, reason = ct.tip(prices, NOW, SETTINGS, CAP, _ctx(other=6.0, month=month))
    assert result is None, "a window that raises the power charge must not be tipped"


def test_the_rise_uses_the_third_highest_hour():
    month = {_local(2026, 11, 2, h): kw for h, kw in ((8, 9.0), (9, 8.0), (10, 7.6))}
    window = {_local(2026, 11, 14, 18): 8.5}
    assert ct.charge_rise_kw(window, month, CAP) == pytest.approx(0.4)


def test_the_counted_peak_uses_the_night_factor_and_the_other_load():
    window = ct.window_at(_prices(), _local(2026, 11, 14, 23), 5.5, SETTINGS, CAP, _ctx())
    assert window.peak_kw == pytest.approx((3.1 + 5.5) * 0.8)


def test_the_mail_names_the_start_and_the_saving():
    result, _ = ct.tip(_prices(EVENING), NOW, SETTINGS, CAP, _ctx(month=HIGH_BASE))
    text = ct.describe(result)
    assert "Start the car at" in text and "EUR more" in text
    assert "11.2 kW" in text


def test_settings_refuse_a_missing_key():
    with pytest.raises(ValueError, match="kw_low"):
        ct.load_tip_settings({"session_kwh": 45})


def _wrapper():
    path = Path(__file__).resolve().parents[2] / "car_tip.py"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMain:
    @pytest.fixture
    def run(self, monkeypatch):
        module = _wrapper()
        config = Mock()
        config.influxdb_bucket_load_control = "load_control"
        config.get = Mock(return_value=None)
        monkeypatch.setattr(module, "get_config", lambda: config)
        monkeypatch.setattr(module, "InfluxClient", Mock())
        monkeypatch.setattr(module.lb, "load_budget_settings", Mock())
        monkeypatch.setattr(module.ct, "load_tip_settings", Mock())
        monkeypatch.setattr(module.pc, "load_cap_settings", Mock())
        monkeypatch.setattr(module, "hour_prices", Mock(return_value={}))
        monkeypatch.setattr(module, "context", Mock(return_value={}))
        window = Mock(start=_local(2026, 11, 14, 17))
        monkeypatch.setattr(module.ct, "tip", Mock(return_value=((window, window, None), "")))
        monkeypatch.setattr(module.ct, "describe", Mock(return_value="tip"))
        sent = Mock(return_value=True)
        monkeypatch.setattr(module, "send_alert_email", sent)
        monkeypatch.setattr(sys, "argv", ["x"])
        monkeypatch.delenv("STAGING_MODE", raising=False)
        return module, config, sent

    def test_staging_never_mails(self, run, monkeypatch):
        module, config, sent = run
        monkeypatch.setenv("STAGING_MODE", "true")
        assert module.main() == 0
        sent.assert_not_called()

    def test_no_mail_settings_is_an_error(self, run):
        module, config, sent = run
        assert module.main() == 1
        sent.assert_not_called()

    def test_production_mails_the_tip(self, run):
        module, config, sent = run
        settings = {"RESEND_API_KEY": "k", "ALERT_EMAIL_TO": "t", "ALERT_EMAIL_FROM": None}
        config.get = Mock(side_effect=lambda key: settings[key])
        assert module.main() == 0
        assert sent.call_args.args[2].startswith("[RedHouse] Car tip: start at")
