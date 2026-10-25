"""CheckWatt reads Helsinki wall times. On 2026-10-25 the collector asked
from an ambiguous 03:00, the API answered from its second pass, and the
points were labelled from the first: one hour was written an hour early."""

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


def _runs(first_utc, last_utc):
    """The collector's real fire times: every 5 minutes at :02:30."""
    times = pd.date_range(first_utc, last_utc, freq="5min", tz="UTC")
    return [t + pd.Timedelta(minutes=2, seconds=30) for t in times]


def _instant(wall):
    return HELSINKI.localize(datetime.datetime.fromisoformat(wall), is_dst=None)


@pytest.mark.parametrize(
    "first, last",
    [("2026-10-24 23:00", "2026-10-25 03:00"), ("2027-03-27 23:00", "2027-03-28 03:00")],
    ids=["autumn", "spring"],
)
def test_every_run_asks_for_a_range_that_names_one_instant_and_covers_the_hour(first, last):
    for run in _runs(first, last):
        with freeze_time(run.to_pydatetime()):
            start, end = _compute_date_range(True, None, None)
        now = run.to_pydatetime()
        assert _instant(start) <= now - datetime.timedelta(hours=1), (run, start)
        assert _instant(end) > now, (run, end)


def test_the_run_after_the_repeated_hour_asks_from_0200():
    with freeze_time("2026-10-25 02:02:31"):  # 04:02:31 winter time
        start, _ = _compute_date_range(True, None, None)
    assert start == "2026-10-25T02:00:00"


def _response(date_from, minutes=3):
    meters = [{"Measurements": [{"Value": float(i)} for i in range(minutes)]}]
    return {"Grouping": "delta", "DateFrom": date_from, "Meters": meters * len(CHECKWATT_COLUMNS)}


def test_points_are_labelled_from_helsinki_time_whatever_the_host_zone():
    points = process_checkwatt_data(_response("2026-10-25T02:00:00"))
    first = datetime.datetime(2026, 10, 24, 23, 0, tzinfo=datetime.timezone.utc)
    assert points[0]["epoch_timestamp"] == int(first.timestamp())


@pytest.mark.parametrize(
    "date_from, error",
    [
        ("2026-10-25T03:00:00", pytz.exceptions.AmbiguousTimeError),
        ("2027-03-28T03:30:00", pytz.exceptions.NonExistentTimeError),
    ],
)
def test_an_ambiguous_or_missing_start_is_refused(date_from, error):
    with pytest.raises(error):
        process_checkwatt_data(_response(date_from))
