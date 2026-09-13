"""Unit tests for health check module."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.monitoring import health_check
from src.monitoring.email_sender import format_alert_body, send_alert_email
from src.monitoring.health_check import (
    DISK_CRITICAL_PERCENT,
    DISK_WARNING_PERCENT,
    check_disk_space,
    check_url_reachable,
)

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


class TestCheckDiskSpace:
    """Tests for disk space checking."""

    @patch("src.monitoring.health_check.shutil.disk_usage")
    def test_disk_ok(self, mock_usage):
        mock_usage.return_value = MagicMock(
            total=10_000_000_000, used=5_000_000_000, free=5_000_000_000
        )
        failures, warnings = check_disk_space()
        assert not failures
        assert not warnings

    @patch("src.monitoring.health_check.shutil.disk_usage")
    def test_disk_warning(self, mock_usage):
        used = int(10_000_000_000 * DISK_WARNING_PERCENT / 100)
        mock_usage.return_value = MagicMock(
            total=10_000_000_000, used=used, free=10_000_000_000 - used
        )
        failures, warnings = check_disk_space()
        assert not failures
        assert len(warnings) == 1
        assert "warning" in warnings[0].lower()

    @patch("src.monitoring.health_check.shutil.disk_usage")
    def test_disk_critical(self, mock_usage):
        used = int(10_000_000_000 * DISK_CRITICAL_PERCENT / 100)
        mock_usage.return_value = MagicMock(
            total=10_000_000_000, used=used, free=10_000_000_000 - used
        )
        failures, warnings = check_disk_space()
        assert len(failures) == 1
        assert "critical" in failures[0].lower()
        assert not warnings

    @patch("src.monitoring.health_check.shutil.disk_usage")
    def test_disk_error(self, mock_usage):
        mock_usage.side_effect = OSError("Permission denied")
        failures, warnings = check_disk_space()
        assert len(failures) == 1
        assert "Permission denied" in failures[0]


class TestCheckUrlReachable:
    """Tests for URL reachability checking."""

    @patch("src.monitoring.health_check.urllib.request.urlopen")
    def test_url_reachable(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = check_url_reachable("http://localhost/health", "Test")
        assert result is None

    @patch("src.monitoring.health_check.urllib.request.urlopen")
    def test_url_unreachable(self, mock_urlopen):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        result = check_url_reachable("http://localhost/health", "Test")
        assert result is not None
        assert "unreachable" in result.lower()


class TestFormatAlertBody:
    """Tests for alert email formatting."""

    def test_format_with_failures(self):
        body = format_alert_body("pi", ["Disk full", "Timer down"])
        assert "FAILURES:" in body
        assert "Disk full" in body
        assert "Timer down" in body

    def test_format_with_warnings(self):
        body = format_alert_body("pi", [], ["Disk 85%"])
        assert "WARNINGS:" in body
        assert "Disk 85%" in body

    def test_format_with_both(self):
        body = format_alert_body("pi", ["critical"], ["warning"])
        assert "FAILURES:" in body
        assert "WARNINGS:" in body


class TestSendAlertEmail:
    """Tests for Resend email sending."""

    @patch("src.monitoring.email_sender.urllib.request.urlopen")
    def test_send_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = send_alert_email(
            api_key="test_key",
            to_email="test@example.com",
            subject="Test",
            body="Test body",
        )
        assert result is True

    @patch("src.monitoring.email_sender.urllib.request.urlopen")
    def test_send_failure(self, mock_urlopen):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Network error")
        result = send_alert_email(
            api_key="test_key",
            to_email="test@example.com",
            subject="Test",
            body="Test body",
        )
        assert result is False


class TestTimerUnitNames:
    def test_production_names_unprefixed(self):
        with patch.dict("os.environ", {"STAGING_MODE": "false"}):
            assert health_check._timer_unit("redhouse-checkwatt") == "redhouse-checkwatt.timer"

    def test_staging_names_prefixed(self):
        with patch.dict("os.environ", {"STAGING_MODE": "true"}):
            assert (
                health_check._timer_unit("redhouse-checkwatt") == "redhouse-staging-checkwatt.timer"
            )

    def test_prefix_applied_once(self):
        with patch.dict("os.environ", {"STAGING_MODE": "true"}):
            unit = health_check._timer_unit("redhouse-aggregate-analytics-15min")
            assert unit == "redhouse-staging-aggregate-analytics-15min.timer"
            assert unit.count("staging") == 1


class TestCheckSystemdServices:
    """A disabled timer is a deployment choice, not a fault."""

    def _run(self, answers):
        def fake(verb, unit):
            return answers[verb]

        with patch.object(health_check.platform, "system", return_value="Linux"):
            with patch.object(health_check, "_systemctl", side_effect=fake):
                return health_check.check_systemd_services()

    def test_disabled_timers_are_not_failures(self):
        failures, warnings = self._run({"is-enabled": "disabled", "is-active": "inactive"})
        assert failures == []
        assert warnings == []

    def test_enabled_but_inactive_is_a_failure(self):
        failures, _ = self._run({"is-enabled": "enabled", "is-active": "inactive"})
        assert len(failures) == len(health_check.REDHOUSE_SERVICES)
        assert "enabled but inactive" in failures[0]

    def test_enabled_and_active_is_clean(self):
        failures, warnings = self._run({"is-enabled": "enabled", "is-active": "active"})
        assert failures == []
        assert warnings == []

    def test_unreadable_state_warns_not_fails(self):
        failures, warnings = self._run({"is-enabled": "unknown", "is-active": "unknown"})
        assert failures == []
        assert len(warnings) == len(health_check.REDHOUSE_SERVICES)

    def test_skipped_off_linux(self):
        with patch.object(health_check.platform, "system", return_value="Windows"):
            assert health_check.check_systemd_services() == ([], [])


class TestAlertFingerprint:
    def test_order_does_not_matter(self):
        a = health_check.alert_fingerprint(["b", "a"], [])
        b = health_check.alert_fingerprint(["a", "b"], [])
        assert a == b

    def test_different_problems_differ(self):
        a = health_check.alert_fingerprint(["disk full"], [])
        b = health_check.alert_fingerprint(["nas down"], [])
        assert a != b

    def test_failure_and_warning_are_distinct(self):
        a = health_check.alert_fingerprint(["x"], [])
        b = health_check.alert_fingerprint([], ["x"])
        assert a != b

    def test_drifting_measurement_is_the_same_problem(self):
        """Otherwise each reading mails again and the window never holds."""
        a = health_check.alert_fingerprint([], ["InfluxDB using 2.21 GB resident"])
        b = health_check.alert_fingerprint([], ["InfluxDB using 2.34 GB resident"])
        assert a == b

    def test_different_subjects_still_differ_without_numbers(self):
        a = health_check.alert_fingerprint(["redhouse-weather.timer is inactive"], [])
        b = health_check.alert_fingerprint(["redhouse-checkwatt.timer is inactive"], [])
        assert a != b


class TestShouldSendAlert:
    @pytest.fixture
    def state_file(self, tmp_path):
        return tmp_path / "state.json"

    def test_sends_when_no_state_yet(self, state_file):
        assert health_check.should_send_alert("abc", state_file, now=NOW) is True

    def test_suppresses_repeat_inside_window(self, state_file):
        health_check.record_alert_sent("abc", state_file, now=NOW)
        later = NOW + timedelta(hours=1)
        assert health_check.should_send_alert("abc", state_file, now=later) is False

    def test_sends_again_after_window(self, state_file):
        health_check.record_alert_sent("abc", state_file, now=NOW)
        later = NOW + timedelta(hours=7)
        assert health_check.should_send_alert("abc", state_file, now=later) is True

    def test_sends_when_problems_change(self, state_file):
        health_check.record_alert_sent("abc", state_file, now=NOW)
        later = NOW + timedelta(minutes=15)
        assert health_check.should_send_alert("xyz", state_file, now=later) is True

    def test_sends_when_state_is_corrupt(self, state_file):
        state_file.write_text("not json")
        assert health_check.should_send_alert("abc", state_file, now=NOW) is True

    def test_sends_when_state_lacks_keys(self, state_file):
        state_file.write_text(json.dumps({"fingerprint": "abc"}))
        assert health_check.should_send_alert("abc", state_file, now=NOW) is True

    def test_record_creates_parent_directory(self, tmp_path):
        nested = tmp_path / "data" / "state.json"
        health_check.record_alert_sent("abc", nested, now=NOW)
        assert json.loads(nested.read_text())["fingerprint"] == "abc"

    def test_record_survives_unwritable_path(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file")
        health_check.record_alert_sent("abc", blocker / "state.json", now=NOW)

    def test_the_flood_scenario(self, state_file):
        """96 runs a day with one standing problem must mail 4 times."""
        sent = 0
        for run in range(96):
            moment = NOW + timedelta(minutes=15 * run)
            if health_check.should_send_alert("standing", state_file, now=moment):
                health_check.record_alert_sent("standing", state_file, now=moment)
                sent += 1
        assert sent == 4


class TestStateFileDefault:
    def test_default_is_under_data(self):
        assert health_check.ALERT_STATE_FILE == Path("data/health_check_state.json")
