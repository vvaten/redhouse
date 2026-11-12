"""The bucket swap renames only after an exact match, always leaves a bucket
with the real name, and deletes nothing."""

import datetime
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "deployment"))

import swap_bucket_shards as sbs  # noqa: E402

from deployment import bucket_rename as br  # noqa: E402
from src.tools.replay_json_logs import _filter_logs_by_time  # noqa: E402

UTC = datetime.timezone.utc
NOW = datetime.datetime.now(UTC)
START = datetime.datetime(2026, 1, 1, tzinfo=UTC)
X, X_NEW, X_OLD = "spotprice", "spotprice_new", "spotprice_old"
# The tests run with the rename moment set to now; this is the real one
NEXT_RENAME_TIME = br.next_rename_time


def _at(*args):
    return datetime.datetime(*args, tzinfo=UTC)


def _ns(moment):
    return int(moment.timestamp()) * 1_000_000_000


def _days(n):
    return datetime.timedelta(days=n)


class Record:
    """A query record as the client returns it."""

    def __init__(self, measurement, field, ns, value, tags):
        self.values = {"result": "_result", "table": 0, "ns": ns, **dict(tags)}
        self._row = (measurement, field, value)

    def get_measurement(self):
        return self._row[0]

    def get_field(self):
        return self._row[1]

    def get_value(self):
        return self._row[2]


class FakeInflux:
    """Points per bucket ID; queries and writes find the bucket by its name,
    and a name is unique, as in InfluxDB."""

    def __init__(self, every=0):
        self.names, self.data, self.meta = {}, {}, {}
        self.renames, self.fail_rename = [], {}
        self.drop_writes = False
        self.source = self._add(X, every)

    def _add(self, name, every, created_at=NOW - _days(900)):
        bucket_id = f"id{len(self.names)}"
        rule = SimpleNamespace(every_seconds=every, shard_group_duration_seconds=604800)
        self.names[bucket_id], self.data[bucket_id] = name, {}
        self.meta[bucket_id] = SimpleNamespace(
            rules=[rule], created_at=created_at, updated_at=created_at
        )
        return bucket_id

    def put(self, name, moment, field="price", value=1.0, tags=()):
        self.data[self._id(name)][("spot", field, _ns(moment), tags)] = value

    def points(self, name):
        return dict(self.data[self._id(name)])

    def _id(self, name):
        return next(i for i, n in self.names.items() if n == name)

    def _bucket(self, bucket_id):
        meta = self.meta[bucket_id]
        return SimpleNamespace(
            id=bucket_id,
            name=self.names[bucket_id],
            org_id="org",
            description=None,
            retention_rules=meta.rules,
            created_at=meta.created_at,
            updated_at=meta.updated_at,
        )

    def buckets_api(self):
        api = MagicMock()
        api.find_bucket_by_name.side_effect = lambda n: (
            self._bucket(self._id(n)) if n in self.names.values() else None
        )
        api.find_bucket_by_id.side_effect = lambda i: self._bucket(i)
        api.create_bucket.side_effect = self._create
        api.update_bucket.side_effect = self._update
        return api

    def _create(self, bucket_name, retention_rules, org_id, description):
        assert retention_rules.shard_group_duration_seconds == 365 * 86400
        return self._bucket(self._add(bucket_name, 0, created_at=NOW))

    def _update(self, bucket):
        key = (bucket.id, bucket.name)
        how = self.fail_rename[key] if key in self.fail_rename else None
        if how == "before":
            raise RuntimeError("timeout")
        if any(n == bucket.name and i != bucket.id for i, n in self.names.items()):
            raise RuntimeError("conflict: bucket name already exists")
        self.renames.append((self.names[bucket.id], bucket.name))
        self.names[bucket.id] = bucket.name
        self.meta[bucket.id].updated_at = datetime.datetime.now(UTC)
        if how == "after":
            raise RuntimeError("timeout after the rename")

    def query_api(self):
        api = MagicMock()
        api.query.side_effect = self._query
        api.query_stream.side_effect = self._stream
        return api

    def _rows(self, flux):
        name = re.search(r'from\(bucket: "([^"]+)"\)', flux).group(1)
        span = re.search(r"range\(start: ([^,]+), stop: ([^)]+)\)", flux)
        lo, hi = (_ns(datetime.datetime.fromisoformat(s)) for s in span.groups())
        rows = self.data[self._id(name)].items()
        return [(k, v) for k, v in rows if lo <= k[2] < hi]

    def _query(self, flux):
        rows = self._rows(flux)
        if "limit(n: 1)" in flux:
            return [SimpleNamespace(records=[Record(*k[:3], v, k[3]) for k, v in rows[:1]])]
        assert "group(" not in flux, "counts must be per series"
        totals = {}
        for k, _ in rows:
            series = (k[0], k[1], k[3])
            totals[series] = totals[series] + 1 if series in totals else 1
        return [
            SimpleNamespace(records=[Record(m, f, 0, n, tags)])
            for (m, f, tags), n in totals.items()
        ]

    def _stream(self, flux):
        assert "ns: int(v: r._time)" in flux
        return [Record(k[0], k[1], k[2], v, k[3]) for k, v in self._rows(flux)]

    def write_api(self, write_options):
        return SimpleNamespace(write=self._write)

    def _write(self, bucket, record):
        for point in record[:-1] if self.drop_writes else record:
            head, fields, ns = point.to_line_protocol().rsplit(" ", 2)
            measurement, *tags = head.split(",")
            field, raw = fields.split("=", 1)
            key = (measurement, field, int(ns), tuple(tuple(t.split("=", 1)) for t in tags))
            self.data[self._id(bucket)][key] = _parse_value(raw)


def _parse_value(raw):
    """A line protocol field value; strings here have no spaces."""
    if raw.startswith('"'):
        return raw[1:-1]
    if raw in ("true", "false"):
        return raw == "true"
    if raw.endswith("i"):
        return int(raw[:-1])
    return float(raw)


@pytest.fixture(autouse=True)
def no_waiting():
    with patch.object(sbs.time, "sleep"), patch.object(sbs.signal, "signal"):
        with patch.object(br, "next_rename_time", side_effect=lambda now: now):
            yield


def _filled(**kwargs):
    influx = FakeInflux(**kwargs)
    for days in (200, 100, 30, 3):
        influx.put(X, NOW - _days(days))
    influx.put(X, NOW - _days(30), tags=(("area", "FI"),))
    influx.put(X, NOW + _days(1), field="forecast")
    return influx


def _copy(influx, dry_run=False):
    return sbs.run_copy(influx, influx.buckets_api(), X, START, dry_run)


def _cutover(influx, live=False):
    return sbs.run_cutover(influx, influx.buckets_api(), X, START, live)


def _verify(influx):
    return sbs.run_verify(influx, influx.buckets_api(), X, START)


def test_a_range_is_cut_into_whole_steps_and_a_rest():
    pieces = sbs.chunks(_at(2026, 11, 1), _at(2026, 11, 17), _days(7))
    assert pieces == [
        (_at(2026, 11, 1), _at(2026, 11, 8)),
        (_at(2026, 11, 8), _at(2026, 11, 15)),
        (_at(2026, 11, 15), _at(2026, 11, 17)),
    ]


def test_a_point_keeps_its_tags_its_integer_type_and_its_nanoseconds():
    record = Record("temperatures", "Olohuone", 1_762_900_000_123_456_789, 21, [("unit", "C")])
    line = sbs.to_point(record).to_line_protocol()
    assert line == "temperatures,unit=C Olohuone=21i 1762900000123456789"


def test_an_empty_value_is_refused_not_dropped():
    with pytest.raises(SystemExit):
        sbs.to_point(Record("m", "note", 1, None, ()))


def test_a_failed_write_is_tried_again_and_the_last_failure_raised():
    write_api = MagicMock()
    write_api.write.side_effect = [RuntimeError("busy"), None]
    sbs.write_batch(write_api, "b", ["p"])
    assert write_api.write.call_count == 2
    write_api.write.side_effect = RuntimeError("down")
    with pytest.raises(RuntimeError):
        sbs.write_batch(write_api, "b", ["p"])


def test_counts_tell_series_with_other_tags_apart():
    influx = _filled()
    assert _copy(influx) == 0
    key = next(k for k in influx.data[influx._id(X_NEW)] if k[3])
    value = influx.data[influx._id(X_NEW)].pop(key)
    influx.data[influx._id(X_NEW)][key[:3] + ((("area", "SE"),),)] = value
    assert sbs.compare(influx.query_api(), X, X_NEW, START, NOW)


def test_the_copy_holds_every_point_and_renames_nothing():
    influx = _filled()
    influx.put(X, NOW - _days(20), field="command", value="ON")
    influx.put(X, NOW - _days(20), field="is_on", value=True)
    with patch.object(sbs, "BATCH", 2):
        assert _copy(influx) == 0
    assert influx.points(X_NEW) == influx.points(X)
    assert influx.renames == []


def test_a_field_with_two_types_is_refused_and_the_partial_copy_named(capsys):
    influx = _filled()
    influx.put(X, NOW - _days(10), value=0)
    with pytest.raises(SystemExit, match="holds float and int"):
        _copy(influx)
    assert "spotprice_new is partial" in capsys.readouterr().out


@pytest.mark.parametrize(
    "now, expected",
    [
        (_at(2026, 11, 10, 20, 0, 0), _at(2026, 11, 10, 20, 9, 45)),
        (_at(2026, 11, 10, 20, 9, 45), _at(2026, 11, 10, 20, 24, 45)),
        (_at(2026, 11, 10, 20, 55, 0), _at(2026, 11, 10, 21, 9, 45)),
    ],
)
def test_the_rename_moment_is_clear_of_every_timer(now, expected):
    assert NEXT_RENAME_TIME(now) == expected


def test_a_rename_moment_too_far_away_renames_nothing():
    influx = _copied()
    with patch.object(br, "next_rename_time", side_effect=lambda now: now + _days(1) / 96):
        assert _cutover(influx) == 1
    assert influx.renames == []


def test_a_copy_that_lost_points_stops_and_keeps_the_new_bucket():
    influx = _filled()
    influx.drop_writes = True
    assert _copy(influx) == 1
    assert set(influx.names.values()) == {X, X_NEW}


@pytest.mark.parametrize("days", [-500, 401])
def test_points_outside_the_copied_range_are_refused(days):
    influx = _filled()
    influx.put(X, (NOW if days > 0 else START) + _days(days))
    with pytest.raises(SystemExit):
        _copy(influx)
    assert set(influx.names.values()) == {X}


def test_a_leftover_new_or_old_bucket_or_an_expiring_bucket_stops_the_copy():
    for leftover in (X_NEW, X_OLD):
        influx = _filled()
        influx._add(leftover, 0)
        with pytest.raises(SystemExit):
            _copy(influx)
    influx = _filled(every=30 * 86400)
    with pytest.raises(SystemExit):
        _copy(influx)
    assert set(influx.names.values()) == {X}


def test_a_dry_run_creates_nothing():
    influx = _filled()
    assert _copy(influx, dry_run=True) == 0
    assert set(influx.names.values()) == {X}


def _copied():
    influx = _filled()
    assert _copy(influx) == 0
    # Written to X while the copy ran, and a rewrite of a recent value
    influx.put(X, NOW - datetime.timedelta(hours=1))
    influx.put(X, NOW - _days(3), value=2.0)
    return influx


def test_the_cutover_catches_up_then_swaps_the_names():
    influx = _copied()
    original = influx.points(X)
    assert _cutover(influx) == 0
    assert influx.renames == [(X, X_OLD), (X_NEW, X)]
    assert influx.points(X) == original
    assert influx.points(X_OLD) == original


def test_a_write_just_before_the_renames_is_copied_after_them():
    influx = _copied()
    real = sbs.swap_names

    def late_write(buckets_api, source, target):
        influx.put(X, NOW - datetime.timedelta(minutes=1))
        return real(buckets_api, source, target)

    with patch.object(sbs, "swap_names", side_effect=late_write):
        assert _cutover(influx) == 0
    assert influx.points(X) == influx.points(X_OLD)


def test_a_cutover_that_does_not_match_renames_nothing():
    influx = _copied()
    influx.drop_writes = True
    assert _cutover(influx) == 1
    assert influx.renames == []


def test_a_rename_that_failed_on_the_way_back_still_counts():
    influx = _copied()
    influx.fail_rename[(influx.source, X_OLD)] = "after"
    assert _cutover(influx) == 0
    assert set(influx.names.values()) == {X, X_OLD}


def test_a_failed_first_rename_changes_nothing():
    influx = _copied()
    before = dict(influx.names)
    influx.fail_rename[(influx.source, X_OLD)] = "before"
    assert _cutover(influx) == 2
    assert influx.names == before


def test_a_failed_second_rename_undoes_the_first():
    influx = _copied()
    before = dict(influx.names)
    influx.fail_rename[(influx._id(X_NEW), X)] = "before"
    assert _cutover(influx) == 2
    assert influx.names == before


@pytest.mark.parametrize("state", [(X_OLD, X_NEW), (X_OLD, X)])
def test_restore_gives_x_its_name_back_from_any_half_done_state(state):
    influx = _copied()
    target = influx._id(X_NEW)
    influx.names[influx.source], influx.names[target] = state
    source_bucket, target_bucket = influx._bucket(influx.source), influx._bucket(target)
    br.restore_names(influx.buckets_api(), source_bucket, target_bucket, X)
    assert influx.names[influx.source] == X and influx.names[target] == X_NEW


def test_an_interrupt_between_the_renames_restores_the_names():
    influx = _copied()
    before = dict(influx.names)
    real = br.rename
    calls = []

    def interrupted(buckets_api, bucket_id, name):
        calls.append(name)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real(buckets_api, bucket_id, name)

    with patch.object(br, "rename", side_effect=interrupted):
        with pytest.raises(KeyboardInterrupt):
            _cutover(influx)
    assert influx.names == before


def _with_live_writes(influx):
    """A writer that adds a point now each time the cut-over copies."""
    real = sbs.copy_all

    def copy_then_write(client, source, target, start, stop):
        copied = real(client, source, target, start, stop)
        influx.put(X, datetime.datetime.now(UTC))
        return copied

    return patch.object(sbs, "copy_all", side_effect=copy_then_write)


def test_a_live_append_only_writer_does_not_stop_the_cutover(capsys):
    influx = _copied()
    with _with_live_writes(influx):
        assert _cutover(influx, live=True) == 0
    assert influx.renames == [(X, X_OLD), (X_NEW, X)]
    assert set(influx.points(X_OLD)) <= set(influx.points(X))
    out = capsys.readouterr().out
    assert "--source temperature --start M --stop M" in out
    minutes = re.search(r"in the minutes ([\d_, ]+) \(Pi time\)", out).group(1).split(", ")
    assert minutes
    # A log file of each minute, named in Pi time as json_logger names it
    for minute in minutes:
        files = [Path(f"{minute}05.json"), Path(f"{minute}59.json")]
        assert _filter_logs_by_time(files, minute, minute) == files


@pytest.mark.parametrize("outcome", [False, KeyboardInterrupt])
def test_the_replay_hint_shows_when_the_renames_fail_or_stop(capsys, outcome):
    influx = _copied()
    kwargs = {"side_effect": outcome} if outcome is not False else {"return_value": False}
    with patch.object(sbs, "swap_names", **kwargs):
        if outcome is False:
            assert _cutover(influx, live=True) == 2
        else:
            with pytest.raises(KeyboardInterrupt):
                _cutover(influx, live=True)
    out = capsys.readouterr().out
    assert "journalctl -u redhouse-temperature.service" in out
    assert "replay_json_logs --source temperature --start M --stop M --dry-run" in out


def test_a_signal_raises_an_interrupt_and_a_lost_terminal_does_not_stop_the_rollback():
    with pytest.raises(KeyboardInterrupt):
        sbs._interrupt(15, None)
    with patch("builtins.print", side_effect=OSError(5, "Input/output error")):
        br.say("the terminal is gone")


def test_without_live_a_growing_bucket_stops_the_cutover():
    influx = _copied()
    with _with_live_writes(influx):
        assert _cutover(influx) == 1
    assert influx.renames == []


def test_live_still_wants_every_older_point():
    influx = _copied()
    influx.drop_writes = True
    assert _cutover(influx, live=True) == 1
    assert influx.renames == []


def test_only_an_append_only_bucket_may_keep_its_writers_running(capsys):
    assert sbs.parse_args(["cutover", "temperatures", "--writers-running"]).writers_running
    for argv in (
        ["cutover", "weather", "--writers-running"],
        ["cutover", "temperatures", "--writers-running", "--writers-stopped"],
    ):
        with pytest.raises(SystemExit):
            sbs.parse_args(argv)
    with patch.object(sbs, "load_dotenv_values", return_value={}):
        with patch.object(sbs, "_client", return_value=MagicMock()):
            with patch.object(sbs, "run_cutover", return_value=0):
                sbs.main(["cutover", "temperatures", "--writers-running"])
    assert "systemctl start" not in capsys.readouterr().out


def test_a_failed_rollback_says_how_to_restore_the_name(capsys):
    influx = _copied()
    influx.fail_rename[(influx._id(X_NEW), X)] = "before"
    influx.fail_rename[(influx.source, X)] = "before"
    assert _cutover(influx) == 2
    assert influx.names[influx.source] == X_OLD
    out = capsys.readouterr().out
    assert "ROLLBACK FAILED" in out and f"--id {influx.source} --name {X}" in out


def test_verify_wants_an_exact_match_before_the_swap_and_allows_new_points_after():
    influx = _copied()
    assert _cutover(influx) == 0
    influx.put(X, NOW)
    assert _verify(influx) == 0
    influx.put(X, NOW - _days(50), field="extra")
    assert _verify(influx) == 1


def test_verify_finds_a_point_missing_after_the_swap():
    influx = _copied()
    assert _cutover(influx) == 0
    influx.put(X_OLD, NOW - datetime.timedelta(hours=2), field="lost")
    assert _verify(influx) == 1


def test_a_cutover_always_ends_with_the_command_to_start_the_writers(capsys):
    with patch.object(sbs, "load_dotenv_values", return_value={}):
        with patch.object(sbs, "_client", return_value=MagicMock()):
            with patch.object(sbs, "run_cutover", side_effect=RuntimeError("NAS gone")):
                with pytest.raises(RuntimeError):
                    sbs.main(["cutover", "weather", "--writers-stopped"])
    assert "sudo systemctl start redhouse-weather.timer" in capsys.readouterr().out


def test_a_cutover_needs_the_writers_confirmed_stopped():
    with pytest.raises(SystemExit):
        sbs.parse_args(["cutover", "weather"])
    assert sbs.parse_args(["cutover", "weather", "--writers-stopped"]).writers_stopped
    assert sbs.writers("weather") == "redhouse-weather.timer"
    assert "swaptest" in sbs.writers("swaptest")
