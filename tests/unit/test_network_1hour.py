"""The hourly ping summary matches what the minutes' raw replies give."""

import argparse
import datetime
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.aggregation import network_1hour as n1h

UTC = datetime.timezone.utc


def _minute(replies, sent=20):
    """A minute point as the ping logger writes it, from its replies."""
    fields = {
        "stats_packets_sent": sent,
        "stats_packets_returned": len(replies),
        "stats_packets_lost": sent - len(replies),
    }
    if replies:
        fields["rtt_avg_ms"] = float(np.mean(replies))
        fields["rtt_std_ms"] = float(np.std(replies))
        fields["rtt_min_ms"] = float(np.min(replies))
        fields["rtt_max_ms"] = float(np.max(replies))
        for p in n1h.PERCENTILES:
            fields[f"rtt_p{p}_ms"] = float(np.percentile(replies, p))
    return fields


def _record(time, fields):
    record = MagicMock()
    record.values = {"_time": time, "result": "_result", **fields}
    record.get_time.return_value = time
    return record


def test_the_mean_and_deviation_match_all_replies_pooled():
    rng = np.random.default_rng(1)
    minutes = [list(rng.normal(2.0 + i % 5, 0.3 + i % 3, size=20 - i % 4)) for i in range(60)]
    fields = n1h.summarize([_minute(m) for m in minutes])
    replies = np.concatenate(minutes)
    assert fields["rtt_avg_ms"] == pytest.approx(np.mean(replies), abs=0.001)
    assert fields["rtt_std_ms"] == pytest.approx(np.std(replies), abs=0.001)
    assert fields["rtt_min_ms"] == pytest.approx(np.min(replies))
    assert fields["rtt_max_ms"] == pytest.approx(np.max(replies))


def test_counts_are_sums_and_the_ratios_come_from_them():
    minutes = [_minute([2.0] * 20), _minute([2.0] * 15), _minute([])]
    fields = n1h.summarize(minutes)
    assert (fields["stats_packets_sent"], fields["stats_packets_returned"]) == (60, 35)
    assert fields["stats_packets_lost"] == 25
    assert fields["stats_lost_ratio"] == pytest.approx(25 / 60)
    assert fields["stats_success_ratio"] == pytest.approx(35 / 60)
    assert (fields["minutes"], fields["minutes_down"]) == (3, 1)
    # The counts stay integers, as in the raw bucket
    assert all(isinstance(fields[k], int) for k in ("stats_packets_sent", "minutes"))


def test_an_hour_with_no_reply_has_counts_but_no_times():
    fields = n1h.summarize([_minute([]), _minute([])])
    assert fields["minutes_down"] == 2
    assert not [k for k in fields if k.startswith("rtt_")]


def test_minutes_go_to_the_hour_they_fall_in():
    hour = datetime.datetime(2026, 11, 7, 10, tzinfo=UTC)
    records = [
        _record(hour + datetime.timedelta(minutes=m), _minute([2.0] * 20)) for m in range(0, 90)
    ]
    records.append(_record(hour, _minute([], sent=0)))
    hours = n1h.hourly(records)
    assert list(hours) == [hour, hour + n1h.HOUR]
    assert [h["minutes"] for h in hours.values()] == [60, 30]


def test_a_minute_without_its_counts_is_skipped():
    hour = datetime.datetime(2026, 11, 7, 10, tzinfo=UTC)
    whole = _minute([2.0] * 20)
    partial = {k: v for k, v in whole.items() if k != "stats_packets_lost"}
    hours = n1h.hourly([_record(hour, whole), _record(hour, partial)])
    assert hours[hour]["minutes"] == 1


def test_an_end_before_the_start_is_refused():
    with pytest.raises(SystemExit) as refused:
        n1h.main(["--start", "2026-11-07", "--end", "2026-11-05"])
    assert refused.value.code == 2


def test_an_invalid_date_is_refused_before_any_query():
    with pytest.raises(SystemExit) as refused:
        n1h.main(["--start", "2026-02-30", "--end", "2026-03-01"])
    assert refused.value.code == 2


def test_a_run_recomputes_the_last_finished_hours():
    now = datetime.datetime(2026, 11, 7, 18, 12, 30, tzinfo=UTC)
    args = argparse.Namespace(start=None, end=None)
    assert n1h._ranges(args, now) == [
        (
            datetime.datetime(2026, 11, 7, 14, tzinfo=UTC),
            datetime.datetime(2026, 11, 7, 18, tzinfo=UTC),
        )
    ]


def test_a_backfill_goes_by_the_day_and_stops_at_the_current_hour():
    now = datetime.datetime(2026, 11, 7, 18, 12, tzinfo=UTC)
    args = argparse.Namespace(start=datetime.date(2026, 11, 5), end=datetime.date(2026, 11, 7))
    ranges = n1h._ranges(args, now)
    assert [r[0].day for r in ranges] == [5, 6, 7]
    assert ranges[-1][1] == datetime.datetime(2026, 11, 7, 18, tzinfo=UTC)


def _client(records):
    table = MagicMock()
    table.records = records
    client = MagicMock()
    client.config.influxdb_bucket_network = "network_b"
    client.config.influxdb_bucket_network_1hour = "network_1hour_b"
    client.query_with_retry.return_value = [table]
    return client


def test_the_hours_are_written_to_the_hourly_bucket_at_their_start():
    hour = datetime.datetime(2026, 11, 7, 10, tzinfo=UTC)
    client = _client([_record(hour + datetime.timedelta(minutes=5), _minute([2.0] * 20))])
    assert n1h.aggregate(client, hour, hour + n1h.HOUR, dry_run=False) == 1
    assert 'from(bucket: "network_b")' in client.query_with_retry.call_args.args[0]
    kwargs = client.write_with_retry.call_args.kwargs
    assert kwargs["bucket"] == "network_1hour_b"
    line = kwargs["record"][0].to_line_protocol()
    assert line.startswith("network ") and "stats_lost_ratio=0," in line
    # Integers stay integers: a type change is refused by InfluxDB
    assert "stats_packets_sent=20i" in line and "minutes=1i" in line
    assert line.endswith(str(int(hour.timestamp() * 1e9)))


def test_a_dry_run_writes_nothing():
    hour = datetime.datetime(2026, 11, 7, 10, tzinfo=UTC)
    client = _client([_record(hour, _minute([2.0] * 20))])
    assert n1h.aggregate(client, hour, hour + n1h.HOUR, dry_run=True) == 1
    client.write_with_retry.assert_not_called()
