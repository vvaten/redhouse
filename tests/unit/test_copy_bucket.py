"""Unit tests for deployment/copy_bucket.py argument and window handling."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "deployment"))

import copy_bucket  # noqa: E402


def _args(*argv):
    return copy_bucket.parse_args(["--source", "a", "--dest", "b", *argv])


class TestResolveTimeRange:
    def test_start_end_inclusive(self):
        start, end = copy_bucket.resolve_time_range(
            _args("--start", "2025-02-27", "--end", "2025-03-01")
        )
        assert start == datetime(2025, 2, 27)
        assert end == datetime(2025, 3, 2)

    def test_days_ends_now(self):
        before = datetime.utcnow()
        start, end = copy_bucket.resolve_time_range(_args("--days", "3"))
        assert end - start == timedelta(days=3)
        assert end >= before

    def test_start_without_end_rejected(self):
        with pytest.raises(ValueError, match="requires --end"):
            copy_bucket.resolve_time_range(_args("--start", "2025-02-27"))

    def test_end_before_start_rejected(self):
        with pytest.raises(ValueError, match="before --start"):
            copy_bucket.resolve_time_range(_args("--start", "2025-03-01", "--end", "2025-02-01"))

    def test_days_with_end_rejected(self):
        with pytest.raises(ValueError, match="cannot be combined"):
            copy_bucket.resolve_time_range(_args("--days", "1", "--end", "2025-03-01"))


class TestIterDayWindows:
    def test_whole_days(self):
        windows = list(copy_bucket.iter_day_windows(datetime(2025, 1, 1), datetime(2025, 1, 4)))
        assert windows == [
            (datetime(2025, 1, 1), datetime(2025, 1, 2)),
            (datetime(2025, 1, 2), datetime(2025, 1, 3)),
            (datetime(2025, 1, 3), datetime(2025, 1, 4)),
        ]

    def test_partial_last_window(self):
        start = datetime(2025, 1, 1, 12)
        end = datetime(2025, 1, 3, 6)
        windows = list(copy_bucket.iter_day_windows(start, end))
        assert windows[0] == (start, datetime(2025, 1, 2, 12))
        assert windows[-1] == (datetime(2025, 1, 2, 12), end)
        assert len(windows) == 2

    def test_empty_range(self):
        assert list(copy_bucket.iter_day_windows(datetime(2025, 1, 1), datetime(2025, 1, 1))) == []
