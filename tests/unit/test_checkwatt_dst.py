"""CheckWatt reads and gives Helsinki wall times. It reads an ambiguous start
as the second pass, and leaves the second pass out when the range starts
before it, so each minute is labelled from its own wall time."""

import datetime

import pandas as pd
import pytest
import pytz
from freezegun import freeze_time

from src.data_collection.checkwatt import (
    CHECKWATT_COLUMNS,
    HELSINKI,
    _compute_date_range,
    process_checkwatt_data,
)
from tests.unit.checkwatt_api import checkwatt_response

UTC = datetime.timezone.utc


def _runs(first_utc, last_utc):
    """The collector's real fire times: every 5 minutes at :02:30."""
    times = pd.date_range(first_utc, last_utc, freq="5min", tz="UTC")
    return [t + pd.Timedelta(minutes=2, seconds=30) for t in times]


def _api_instant(wall):
    """The instant the API reads from a wall time, the second pass if ambiguous."""
    wall = datetime.datetime.fromisoformat(wall)
    try:
        return HELSINKI.localize(wall, is_dst=None)
    except pytz.AmbiguousTimeError:
        return HELSINKI.localize(wall, is_dst=False)


def _points(start_utc, end_utc):
    """Processed points; with the helper's values each one names its own minute."""
    points = process_checkwatt_data(checkwatt_response(start_utc, end_utc))
    for point in points:
        for column in CHECKWATT_COLUMNS:
            if column in point:
                assert point[column] == point["epoch_timestamp"], (point, column)
    return points


def _utc(text):
    return int(pd.Timestamp(text, tz="UTC").timestamp())


@pytest.mark.parametrize(
    "first, last, lost",
    [
        ("2026-10-24 21:00", "2026-10-25 04:00", ["2026-10-25 00:59"]),
        ("2027-03-27 21:00", "2027-03-28 04:00", []),
    ],
    ids=["autumn", "spring"],
)
def test_the_runs_write_every_minute_at_its_own_moment(first, last, lost):
    written: dict = {column: set() for column in CHECKWATT_COLUMNS}
    for run in _runs(first, last):
        with freeze_time(run.to_pydatetime()):
            start, _ = _compute_date_range(True, None, None)
        now = run.to_pydatetime().replace(second=0)
        for point in _points(_api_instant(start), now):
            for column in CHECKWATT_COLUMNS:
                if column in point:
                    written[column].add(point["epoch_timestamp"])

    window = pd.date_range(pd.Timestamp(first) + pd.Timedelta(hours=1), last, freq="min")
    expected = {int(t.timestamp()) for t in window.tz_localize("UTC")[:-2]}
    # The API gives no delta for the last minute of the first pass
    lost_epochs = {_utc(t) for t in lost}
    assert expected <= written["Battery_SoC"]
    for column in CHECKWATT_COLUMNS[1:]:
        assert expected - lost_epochs <= written[column], column
        assert not lost_epochs & written[column], column


@pytest.mark.parametrize(
    "run, start",
    [
        ("2026-10-25 01:02:31", "2026-10-25T02:00:00"),  # start 03:00 summer time: moved back
        ("2026-10-25 02:02:31", "2026-10-25T03:00:00"),  # start 03:00 winter time: sent as is
    ],
)
def test_an_ambiguous_start_is_sent_only_for_the_second_pass(run, start):
    with freeze_time(run):
        assert _compute_date_range(True, None, None)[0] == start


def test_a_range_from_before_the_repeated_hour_skips_its_second_pass():
    points = {
        p["epoch_timestamp"]: p
        for p in _points(
            datetime.datetime(2026, 10, 24, 23, 58, tzinfo=UTC),
            datetime.datetime(2026, 10, 25, 2, 2, tzinfo=UTC),
        )
    }
    soc = sorted(epoch for epoch, point in points.items() if "Battery_SoC" in point)
    assert soc[61] == _utc("2026-10-25 00:59")  # 03:59 summer time
    assert soc[62] == _utc("2026-10-25 02:00")  # 04:00 winter time
    # The delta dated 04:00 winter time is the minute before it
    assert "BatteryCharge" in points[_utc("2026-10-25 01:59")]
    assert "BatteryCharge" not in points[_utc("2026-10-25 00:59")]


def test_a_range_from_the_second_pass_is_labelled_in_winter_time():
    points = _points(
        datetime.datetime(2026, 10, 25, 1, 0, tzinfo=UTC),
        datetime.datetime(2026, 10, 25, 2, 2, tzinfo=UTC),
    )
    epochs = [p["epoch_timestamp"] for p in points]
    assert epochs == list(range(_utc("2026-10-25 01:00"), _utc("2026-10-25 02:02"), 60))


def test_the_spring_day_has_no_gap_in_real_time():
    points = _points(
        datetime.datetime(2027, 3, 28, 0, 58, tzinfo=UTC),
        datetime.datetime(2027, 3, 28, 2, 2, tzinfo=UTC),
    )
    epochs = [p["epoch_timestamp"] for p in points]
    assert epochs == list(range(_utc("2027-03-28 00:58"), _utc("2027-03-28 02:02"), 60))


def _ordinary_response():
    start = datetime.datetime(2026, 10, 18, 11, 0, tzinfo=UTC)
    return checkwatt_response(start, start + datetime.timedelta(minutes=3))


def test_a_first_minute_away_from_the_utc_start_is_refused():
    response = _ordinary_response()
    response["Meters"][0]["DateFromUTC"] = "2026-10-18T10:00:00"
    with pytest.raises(ValueError, match="DateFromUTC"):
        process_checkwatt_data(response)


def test_wall_times_that_do_not_move_forward_are_refused():
    response = _ordinary_response()
    measurements = response["Meters"][0]["Measurements"]
    measurements[2]["Date"] = measurements[0]["Date"]
    with pytest.raises(ValueError, match="does not follow"):
        process_checkwatt_data(response)
