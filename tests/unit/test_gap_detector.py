"""Tests for the gap detector module."""

import datetime
from unittest.mock import MagicMock

import pytz

from src.aggregation.gap_detector import find_gaps

UTC = pytz.UTC


def _make_record(time_val):
    """Create a mock FluxRecord with the given time."""
    record = MagicMock()
    record.get_time.return_value = time_val
    return record


def _make_table(times):
    """Create a mock FluxTable with records at given times."""
    table = MagicMock()
    table.records = [_make_record(t) for t in times]
    return table


class TestFindGaps:
    """Tests for find_gaps function."""

    def test_no_gaps_when_all_windows_present(self):
        """All expected windows exist - no gaps reported."""
        start = datetime.datetime(2026, 3, 22, 5, 0, tzinfo=UTC)
        end = datetime.datetime(2026, 3, 22, 5, 30, tzinfo=UTC)

        existing = [
            start,
            start + datetime.timedelta(minutes=5),
            start + datetime.timedelta(minutes=10),
            start + datetime.timedelta(minutes=15),
            start + datetime.timedelta(minutes=20),
            start + datetime.timedelta(minutes=25),
        ]

        client = MagicMock()
        client.query_api.query.return_value = [_make_table(existing)]
        client.config.influxdb_org = "area51"

        gaps = find_gaps(client, "emeters_5min", "energy", start, end, 5)
        assert gaps == []

    def test_detects_missing_windows(self):
        """Two windows missing in the middle."""
        start = datetime.datetime(2026, 3, 22, 5, 0, tzinfo=UTC)
        end = datetime.datetime(2026, 3, 22, 5, 30, tzinfo=UTC)

        # Missing 05:20 and 05:25 (window_start timestamps)
        existing = [
            start,
            start + datetime.timedelta(minutes=5),
            start + datetime.timedelta(minutes=10),
            start + datetime.timedelta(minutes=15),
            # 05:20 missing
            # 05:25 missing
        ]

        client = MagicMock()
        client.query_api.query.return_value = [_make_table(existing)]
        client.config.influxdb_org = "area51"

        gaps = find_gaps(client, "emeters_5min", "energy", start, end, 5)

        # Gaps returned as window_end timestamps
        assert len(gaps) == 2
        assert gaps[0] == datetime.datetime(2026, 3, 22, 5, 25, tzinfo=UTC)
        assert gaps[1] == datetime.datetime(2026, 3, 22, 5, 30, tzinfo=UTC)

    def test_all_windows_missing(self):
        """No data at all - all windows reported as gaps."""
        start = datetime.datetime(2026, 3, 22, 5, 0, tzinfo=UTC)
        end = datetime.datetime(2026, 3, 22, 5, 15, tzinfo=UTC)

        client = MagicMock()
        client.query_api.query.return_value = []
        client.config.influxdb_org = "area51"

        gaps = find_gaps(client, "emeters_5min", "energy", start, end, 5)
        assert len(gaps) == 3

    def test_query_failure_returns_empty(self):
        """If InfluxDB query fails, return empty list (don't crash)."""
        start = datetime.datetime(2026, 3, 22, 5, 0, tzinfo=UTC)
        end = datetime.datetime(2026, 3, 22, 5, 30, tzinfo=UTC)

        client = MagicMock()
        client.query_api.query.side_effect = Exception("Connection timeout")
        client.config.influxdb_org = "area51"

        gaps = find_gaps(client, "emeters_5min", "energy", start, end, 5)
        assert gaps == []

    def test_15min_interval(self):
        """Works with 15-minute intervals."""
        start = datetime.datetime(2026, 3, 22, 5, 0, tzinfo=UTC)
        end = datetime.datetime(2026, 3, 22, 6, 0, tzinfo=UTC)

        # Present: 05:00, 05:15, 05:45 -- missing: 05:30
        existing = [
            start,
            start + datetime.timedelta(minutes=15),
            start + datetime.timedelta(minutes=45),
        ]

        client = MagicMock()
        client.query_api.query.return_value = [_make_table(existing)]
        client.config.influxdb_org = "area51"

        gaps = find_gaps(client, "analytics_15min", "analytics", start, end, 15)
        assert len(gaps) == 1
        assert gaps[0] == datetime.datetime(2026, 3, 22, 5, 45, tzinfo=UTC)

    def test_1hour_interval(self):
        """Works with 1-hour intervals."""
        start = datetime.datetime(2026, 3, 22, 0, 0, tzinfo=UTC)
        end = datetime.datetime(2026, 3, 22, 3, 0, tzinfo=UTC)

        # Present: 00:00, 02:00 -- missing: 01:00
        existing = [
            start,
            start + datetime.timedelta(hours=2),
        ]

        client = MagicMock()
        client.query_api.query.return_value = [_make_table(existing)]
        client.config.influxdb_org = "area51"

        gaps = find_gaps(client, "analytics_1hour", "analytics", start, end, 60)
        assert len(gaps) == 1
        assert gaps[0] == datetime.datetime(2026, 3, 22, 2, 0, tzinfo=UTC)
