"""Tests for InfluxClient query_with_retry method."""

from unittest.mock import MagicMock, patch

import pytest

from src.common.influx_client import QUERY_MAX_RETRIES, InfluxClient


def _make_client():
    """Create an InfluxClient with mocked internals (skip __init__)."""
    client = InfluxClient.__new__(InfluxClient)
    client.config = MagicMock()
    client.config.influxdb_org = "area51"
    client.query_api = MagicMock()
    client.write_api = MagicMock()
    client.client = MagicMock()
    return client


def _make_table(n_records=1):
    """Create a mock FluxTable with n records."""
    table = MagicMock()
    table.records = [MagicMock() for _ in range(n_records)]
    return table


class TestQueryWithRetry:
    """Tests for query_with_retry method."""

    def test_success_on_first_attempt(self):
        """Query succeeds on first try - no retry needed."""
        client = _make_client()
        mock_result = [_make_table()]
        client.query_api.query.return_value = mock_result

        result = client.query_with_retry("some flux query")

        assert result == mock_result
        assert client.query_api.query.call_count == 1

    @patch("src.common.influx_client.time.sleep")
    def test_retry_on_timeout(self, mock_sleep):
        """First call times out, second succeeds."""
        client = _make_client()
        mock_result = [_make_table()]
        client.query_api.query.side_effect = [
            Exception("HTTPConnectionPool: Read timed out"),
            mock_result,
        ]

        result = client.query_with_retry("some flux query")

        assert result == mock_result
        assert client.query_api.query.call_count == 2
        mock_sleep.assert_called_once()

    @patch("src.common.influx_client.time.sleep")
    def test_raises_after_max_retries(self, mock_sleep):
        """All retries timeout - raises the last error."""
        client = _make_client()
        client.query_api.query.side_effect = Exception("Read timed out")

        with pytest.raises(Exception, match="Read timed out"):
            client.query_with_retry("some flux query")

        assert client.query_api.query.call_count == QUERY_MAX_RETRIES

    def test_no_retry_on_non_timeout_error(self):
        """Non-timeout errors raise immediately without retry."""
        client = _make_client()
        client.query_api.query.side_effect = Exception("invalid flux syntax")

        with pytest.raises(Exception, match="invalid flux syntax"):
            client.query_with_retry("bad query")

        assert client.query_api.query.call_count == 1

    @patch("src.common.influx_client.time.sleep")
    def test_retry_on_lowercase_timeout(self, mock_sleep):
        """Handles various timeout message formats."""
        client = _make_client()
        mock_result = [_make_table()]
        client.query_api.query.side_effect = [
            Exception("Connection timeout after 10s"),
            mock_result,
        ]

        result = client.query_with_retry("some flux query")

        assert result == mock_result
        assert client.query_api.query.call_count == 2

    def test_passes_correct_org(self):
        """Query is called with the correct org parameter."""
        client = _make_client()
        client.query_api.query.return_value = []

        client.query_with_retry("test query")

        client.query_api.query.assert_called_once_with("test query", org="area51")
