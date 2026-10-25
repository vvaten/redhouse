"""The staging data copy only fills windows where staging has no data."""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "deployment"))

import copy_production_to_staging as cps  # noqa: E402

START = datetime(2026, 10, 25, 0, 7, tzinfo=timezone.utc)
END = datetime(2026, 10, 25, 3, 7, tzinfo=timezone.utc)


def _at(hour, minute=0):
    return datetime(2026, 10, 25, hour, minute, tzinfo=timezone.utc)


def test_every_copied_bucket_but_load_control_has_a_window():
    assert set(cps.GAP_WINDOW_MINUTES) == set(cps.BUCKET_MAPPINGS) - {"load_control"}


def test_load_control_is_copied_only_with_overwrite():
    client = MagicMock()
    assert cps.fill_bucket_gaps(client, "load_control", "load_control_staging", START, END) == 0
    client.query_api.assert_not_called()


def test_only_windows_without_staging_data_are_copied():
    source = {("m", _at(0)), ("m", _at(1))}
    assert cps._gap_ranges(source, {("m", _at(1))}, 60, START, END) == [("m", START, _at(1))]
    assert cps._gap_ranges(source, source, 60, START, END) == []


def test_a_gap_of_one_measurement_is_not_hidden_by_another():
    source = {("plan", _at(0)), ("actual", _at(0))}
    ranges = cps._gap_ranges(source, {("plan", _at(0))}, 60, START, END)
    assert ranges == [("actual", START, _at(1))]


def test_adjacent_gaps_merge_and_staging_only_windows_are_ignored():
    source = {("m", _at(0, 15 * i)) for i in range(1, 4)}
    staging_only = {("m", _at(1, 30))}
    assert cps._gap_ranges(source, staging_only, 15, START, END) == [("m", _at(0, 15), _at(1))]


def test_the_last_two_windows_are_left_to_staging():
    source = {("m", _at(h)) for h in range(4)}
    assert cps._gap_ranges(source, set(), 60, START, END) == [("m", START, _at(2))]


def test_the_clipped_first_window_ends_on_the_grid():
    # Flux gives the first window the range start as its _start
    assert cps._window_start(START, 15) == _at(0)
    assert cps._window_start(_at(0, 22), 15) == _at(0, 15)


def test_the_window_query_counts_per_series_and_keeps_the_measurement():
    query = cps._window_counts_query("checkwatt", START, END, 15)
    assert 'aggregateWindow(every: 15m, fn: count, timeSrc: "_start"' in query
    assert 'keep(columns: ["_time", "_measurement"])' in query


def test_a_measurement_copy_filters_the_range():
    query = cps._build_range_query("emeters", START, END, "energy")
    assert 'r._measurement == "energy"' in query
    assert "_measurement" not in cps._build_range_query("emeters", START, END)


@pytest.mark.parametrize(
    "flag, chosen", [([], "fill_bucket_gaps"), (["--overwrite"], "copy_bucket_data")]
)
def test_overwrite_copies_the_whole_range_and_the_default_fills_gaps(flag, chosen):
    argv = ["copy_production_to_staging.py", "--days", "2", "--buckets", "checkwatt", *flag]
    with (
        patch.object(sys, "argv", argv),
        patch.object(cps, "_connect"),
        patch.object(cps, "get_config"),
        patch.object(cps, "fill_bucket_gaps", return_value=0) as fill,
        patch.object(cps, "copy_bucket_data", return_value=0) as full,
    ):
        assert cps.main() == 0
    used = {"fill_bucket_gaps": fill, "copy_bucket_data": full}
    assert used[chosen].call_count == 1
    assert sum(mock.call_count for mock in used.values()) == 1


def _record(measurement, time):
    record = MagicMock()
    record.get_measurement.return_value = measurement
    record.get_time.return_value = time
    return record


def test_fill_copies_the_gap_ranges_only():
    # Window _start values as Flux gives them: the first one clipped to the range start
    production = [START, _at(0, 15), _at(0, 30)]

    def windows(query):
        times = production if '"checkwatt"' in query else production[1:]
        return [MagicMock(records=[_record("checkwatt", t) for t in times])]

    client = MagicMock()
    client.query_api.return_value.query.side_effect = windows
    with patch.object(cps, "copy_bucket_data", return_value=7) as copy:
        copied = cps.fill_bucket_gaps(
            client, "checkwatt", "checkwatt_staging", START.replace(tzinfo=None), END
        )
    assert copied == 7
    copy.assert_called_once_with(
        client, "checkwatt", "checkwatt_staging", START, _at(0, 15), False, "checkwatt"
    )
