"""Unit tests for health check module."""

from unittest.mock import MagicMock, patch

from src.monitoring.email_sender import format_alert_body, send_alert_email
from src.monitoring.health_check import (
    DISK_CRITICAL_PERCENT,
    DISK_WARNING_PERCENT,
    check_disk_space,
    check_url_reachable,
)


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
