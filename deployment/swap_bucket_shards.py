#!/usr/bin/env python3
"""Move a forever-kept bucket X to 365-day shard groups under the same name.

  copy X       copy X into a new X_new and compare; any time, X keeps working
  cutover X    with X's writers stopped: copy the points written since the
               copy began, compare exactly, rename X to X_old and X_new to X,
               copy and compare once more; an append-only writer may run
  verify X     with the writers running again: X equals X_old up to two days
               before the swap, and lacks none of its points after it

Nothing is deleted; X_old stays until it is deleted by hand.
"""

import argparse
import datetime
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from influxdb_client import BucketRetentionRules, InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

sys.path.insert(0, str(Path(__file__).parent.parent))

from deployment.bucket_rename import (  # noqa: E402
    TIMING,
    print_replay_hint,
    swap_names,
    wait_for_slot,
)

UTC = datetime.timezone.utc
SHARD_SECONDS = 365 * 86400
# Data before this day is checked for and refused, not copied
DEFAULT_START = datetime.datetime(2020, 1, 1, tzinfo=UTC)
# Forecasts and plans reach into the future
FUTURE = datetime.timedelta(days=400)
# Collectors rewrite up to 5 days back (solar prediction), so the cut-over
# copies again from 8 days before X_new was created
RECOPY_BACK = datetime.timedelta(days=8)
QUERY_TIMEOUT_MS = 900_000
BUCKET_TIMEOUT_MS = 30_000
# Read a week, write it in batches: Flux to() was slower and hit its own
# write timeout on this NAS
CHUNK = datetime.timedelta(days=7)
BATCH = 5000
# A breath between batches keeps the live collectors' writes from stalling
BATCH_PAUSE_S = 0.2
WRITE_ATTEMPTS = 3
RETRY_PAUSE_S = 5
# Columns of a query result that are not tags
NOT_TAGS = {"result", "table", "_start", "_stop", "_time", "_value", "_field", "_measurement", "ns"}
# Counts are compared per 30 days, which keeps the number of queries low
VERIFY_CHUNK = datetime.timedelta(days=30)
# The systemd timers on the Pi that write each bucket
WRITERS = {
    "load_control": ["redhouse-generate-program.timer", "redhouse-execute-program.timer"],
    "windpower": ["redhouse-windpower.timer"],
    "spotprice": ["redhouse-spot-prices.timer"],
    "weather": ["redhouse-weather.timer"],
    "checkwatt": ["redhouse-checkwatt.timer"],
    "temperatures": ["redhouse-temperature.timer"],
}
# Writers that only add points at the current time and never rewrite one,
# so they can keep running through the cut-over
APPEND_ONLY = {"temperatures"}
# A run that read before the cut-over can still write a little after it
LIVE_MARGIN = datetime.timedelta(minutes=10)
AVOID = {"spotprice": "Not 13:29-16:00 local time, when the prices arrive."}


def writers(name: str) -> str:
    """The writer timers of a bucket, for the stop and start commands."""
    if name not in WRITERS:
        return f"<the timers that write {name}>"
    return " ".join(WRITERS[name])


def chunks(start: datetime.datetime, stop: datetime.datetime, step=CHUNK) -> list:
    """[start, stop) cut into pieces of step, as (start, stop) pairs."""
    edges = [start]
    while edges[-1] + step < stop:
        edges.append(edges[-1] + step)
    edges.append(stop)
    return list(zip(edges, edges[1:]))


def _span(start: datetime.datetime, stop: datetime.datetime) -> str:
    return f"range(start: {start.isoformat()}, stop: {stop.isoformat()})"


def tags_of(record) -> tuple:
    """The tags of a query record, sorted, as (name, value) pairs."""
    return tuple(
        sorted((k, v) for k, v in record.values.items() if k not in NOT_TAGS and v is not None)
    )


def counts(query_api, bucket: str, start, stop) -> dict:
    """{(measurement, field, tags): number of points} in [start, stop), per series."""
    flux = f'from(bucket: "{bucket}") |> {_span(start, stop)} |> count()'
    return {
        (r.get_measurement(), r.get_field(), tags_of(r)): r.get_value()
        for table in query_api.query(flux)
        for r in table.records
    }


def has_points(query_api, bucket: str, start, stop) -> bool:
    """Whether [start, stop) holds any point; cheap where no shard exists."""
    flux = f'from(bucket: "{bucket}") |> {_span(start, stop)} |> limit(n: 1)'
    return any(table.records for table in query_api.query(flux))


def refuse_points_outside(query_api, bucket: str, start, stop) -> None:
    """Stop when X holds points the copy would leave behind."""
    far_past = datetime.datetime(1970, 1, 1, tzinfo=UTC)
    far_future = datetime.datetime(2200, 1, 1, tzinfo=UTC)
    if has_points(query_api, bucket, far_past, start):
        raise SystemExit(f"{bucket} has points before {start}; pass an earlier --start")
    if has_points(query_api, bucket, stop, far_future):
        raise SystemExit(f"{bucket} has points after {stop}, beyond the copied range")


def to_point(record) -> Point:
    """The point of a query record, with its tags, its field's own type and
    its time in nanoseconds; Python datetimes keep only microseconds."""
    if record.get_value() is None:
        # The client reads an empty string as None, and a Point drops it
        raise SystemExit(
            f"Empty value in {record.get_measurement()} {record.get_field()}"
            f" {tags_of(record)} at {record.values['ns']} ns; it cannot be copied"
        )
    point = Point(record.get_measurement()).field(record.get_field(), record.get_value())
    for name, value in tags_of(record):
        point = point.tag(name, value)
    return point.time(record.values["ns"], WritePrecision.NS)


def write_batch(write_api, bucket: str, points: list) -> None:
    for attempt in range(1, WRITE_ATTEMPTS + 1):
        try:
            write_api.write(bucket=bucket, record=points)
            return
        except Exception as e:
            if attempt == WRITE_ATTEMPTS:
                raise
            print(f"  Write to {bucket}, attempt {attempt}: {e}")
            time.sleep(RETRY_PAUSE_S * attempt)


def check_type(types: dict, record) -> None:
    """A 7-day shard takes a field's type afresh each week; a 365-day shard
    refuses a second type for a year, so refuse it here, before any write."""
    key = (record.get_measurement(), record.get_field())
    kind = type(record.get_value()).__name__
    if key not in types:
        types[key] = kind
    elif types[key] != kind:
        raise SystemExit(
            f"{key[0]} {key[1]} holds {types[key]} and {kind} values"
            f" (at {record.values['ns']} ns); one 365-day shard takes one type"
        )


def copy(client, source: str, target: str, start, stop, types: dict) -> int:
    """Copy [start, stop) through the client; the same points just
    overwrite, so a repeated copy is harmless. Returns the points copied."""
    flux = (
        f'from(bucket: "{source}") |> {_span(start, stop)}'
        " |> map(fn: (r) => ({r with ns: int(v: r._time)}))"
    )
    write_api = client.write_api(write_options=SYNCHRONOUS)
    batch, total = [], 0
    for record in client.query_api().query_stream(flux):
        point = to_point(record)
        check_type(types, record)
        batch.append(point)
        if len(batch) == BATCH:
            write_batch(write_api, target, batch)
            total, batch = total + len(batch), []
            time.sleep(BATCH_PAUSE_S)
    if batch:
        write_batch(write_api, target, batch)
    return total + len(batch)


def copy_all(client, source: str, target: str, start, stop) -> int:
    types: dict = {}
    return sum(copy(client, source, target, a, b, types) for a, b in chunks(start, stop))


def compare(query_api, source: str, target: str, start, stop, at_least: bool = False) -> list:
    """The (start, keys) of each 30 days whose counts differ; with at_least,
    the target may hold more, as the collectors keep writing to it."""
    wrong = []
    for a, b in chunks(start, stop, VERIFY_CHUNK):
        keys = differing(counts(query_api, target, a, b), counts(query_api, source, a, b), at_least)
        if keys:
            wrong.append((a, keys))
    return wrong


def differing(have: dict, want: dict, at_least: bool) -> list:
    """The keys whose count in have is short of want, or not equal to it."""
    got = {k: have[k] if k in have else 0 for k in set(have) | set(want)}
    need = {k: want[k] if k in want else 0 for k in got}
    return sorted(k for k in got if got[k] < need[k] or (not at_least and got[k] != need[k]))


def create_target(buckets_api, source_bucket, name: str):
    """X_new with the retention of X and 365-day shard groups."""
    rules = BucketRetentionRules(
        type="expire", every_seconds=0, shard_group_duration_seconds=SHARD_SECONDS
    )
    return buckets_api.create_bucket(
        bucket_name=name,
        retention_rules=rules,
        org_id=source_bucket.org_id,
        description=source_bucket.description,
    )


def _find(buckets_api, name: str):
    bucket = buckets_api.find_bucket_by_name(name)
    if bucket is None:
        raise SystemExit(f"No bucket {name}")
    return bucket


def _refuse_existing(buckets_api, *names: str) -> None:
    for name in names:
        if buckets_api.find_bucket_by_name(name) is not None:
            raise SystemExit(f"{name} exists already; finish or remove it first")


def run_copy(client, buckets_api, name: str, start: datetime.datetime, dry_run: bool) -> int:
    query_api = client.query_api()
    source = _find(buckets_api, name)
    _refuse_existing(buckets_api, f"{name}_new", f"{name}_old")
    if source.retention_rules[0].every_seconds != 0:
        raise SystemExit(f"{name} expires data; only forever-kept buckets swap")
    began = datetime.datetime.now(UTC)
    refuse_points_outside(query_api, name, start, began + FUTURE)
    if dry_run:
        total = sum(
            sum(counts(query_api, name, a, b).values())
            for a, b in chunks(start, began + FUTURE, VERIFY_CHUNK)
        )
        print(f"DRY RUN: would copy {total} points of {name}")
        return 0

    target = create_target(buckets_api, source, f"{name}_new")
    print(f"Created {name}_new with 365-day shards")
    try:
        print(f"Copied {copy_all(client, name, target.name, start, began + FUTURE)} points")
    except BaseException:
        print(f"The copy stopped and {name}_new is partial; remove it before the next attempt")
        raise
    settled = began - RECOPY_BACK
    wrong = compare(query_api, name, target.name, start, settled)
    if wrong:
        print(f"Counts differ before {settled}, nothing renamed: {wrong[:5]}")
        print(f"Remove {name}_new before the next attempt")
        return 1
    print(f"Counts match up to {settled}")
    if name in APPEND_ONLY:
        print("Next, within 5 minutes before minute 9, 24, 39 or 54, with the writers running:")
        print(f"  swap_bucket_shards.py cutover {name} --writers-running")
        return 0
    print(f"Next, on the Pi: sudo systemctl stop {writers(name)}")
    print(f"and run: swap_bucket_shards.py cutover {name} --writers-stopped")
    print(TIMING)
    if name in AVOID:
        print(AVOID[name])
    return 0


def run_cutover(client, buckets_api, name: str, start: datetime.datetime, live: bool) -> int:
    """X stands still, or with live only grows at its end: bring X_new level
    with it, compare, swap the names, and check once more after the swap."""
    query_api = client.query_api()
    source = _find(buckets_api, name)
    target = _find(buckets_api, f"{name}_new")
    _refuse_existing(buckets_api, f"{name}_old")
    began = datetime.datetime.now(UTC)
    since, stop = target.created_at - RECOPY_BACK, began + FUTURE
    # Exact counts up to here; after it a live writer keeps adding points
    exact_until = began - LIVE_MARGIN if live else stop
    refuse_points_outside(query_api, name, start, stop)
    print(f"Copied {copy_all(client, name, target.name, since, stop)} points again from {since}")
    wrong = compare(query_api, name, target.name, since, exact_until)
    if wrong:
        print(f"Counts differ from {since}, nothing renamed: {wrong[:5]}")
        print("Check that the writers are stopped" if not live else "Run the cutover again")
        return 1
    if not wait_for_slot():
        return 1
    renaming = datetime.datetime.now(UTC)
    try:
        swapped = swap_names(buckets_api, source, target)
    finally:
        if live:
            print_replay_hint(renaming, datetime.datetime.now(UTC))
    if not swapped:
        return 2
    print(f"Renamed {name} to {name}_old and {name}_new to {name}")
    wrong = check_after_swap(client, name, since, exact_until, stop, live)
    if wrong:
        print(f"After the swap the counts differ from {since}: {wrong[:5]}")
        print(f"{name}_old holds every point; do not delete it")
        return 1
    print(f"Counts match. Then run: swap_bucket_shards.py verify {name}")
    return 0


def check_after_swap(client, name: str, since, exact_until, stop, live: bool) -> list:
    """A write that slipped in before the renames is only in X_old. A live
    writer always may have done that; stopped writers keep the stop short,
    so copy only when a count shows it."""
    query_api = client.query_api()
    if live:
        copy_all(client, f"{name}_old", name, since, stop)
    wrong = compare(query_api, f"{name}_old", name, since, exact_until)
    if wrong and not live:
        copy_all(client, f"{name}_old", name, since, stop)
        wrong = compare(query_api, f"{name}_old", name, since, exact_until)
    if live:
        wrong += compare(query_api, f"{name}_old", name, exact_until, stop, at_least=True)
    return wrong


def run_verify(client, buckets_api, name: str, start: datetime.datetime) -> int:
    """After the cut-over, with the writers running: X equals X_old up to two
    days before the swap, and lacks none of X_old's points after it."""
    query_api = client.query_api()
    swapped = _find(buckets_api, f"{name}_old").updated_at
    if swapped is None:
        raise SystemExit(f"{name}_old has no update time to place the swap")
    settled = swapped - datetime.timedelta(days=2)
    stop = datetime.datetime.now(UTC) + FUTURE
    wrong = compare(query_api, f"{name}_old", name, start, settled)
    wrong += compare(query_api, f"{name}_old", name, settled, stop, at_least=True)
    if wrong:
        print(f"{name} differs from {name}_old: {wrong[:5]}")
        return 1
    print(f"Done: {name} holds every point of {name}_old; delete {name}_old by hand later")
    return 0


def parse_args(argv: Optional[list]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("step", choices=("copy", "cutover", "verify"))
    parser.add_argument("bucket", help=f"The forever-kept bucket: {', '.join(sorted(WRITERS))}")
    parser.add_argument(
        "--start",
        type=datetime.date.fromisoformat,
        default=DEFAULT_START.date(),
        help="First day of the data, YYYY-MM-DD (default 2020-01-01)",
    )
    parser.add_argument("--dry-run", action="store_true", help="copy: count, create nothing")
    running = parser.add_mutually_exclusive_group()
    running.add_argument(
        "--writers-stopped",
        action="store_true",
        help="cutover: confirm that the bucket's writer timers are stopped",
    )
    running.add_argument(
        "--writers-running",
        action="store_true",
        help=f"cutover: keep the writers running; only for {', '.join(sorted(APPEND_ONLY))}",
    )
    args = parser.parse_args(argv)
    if args.step == "cutover" and not (args.writers_stopped or args.writers_running):
        parser.error(f"cutover needs --writers-stopped: stop {writers(args.bucket)}")
    if args.writers_running and args.bucket not in APPEND_ONLY:
        parser.error(f"{args.bucket} has writers that rewrite points; stop them instead")
    return args


def _client(env: dict, timeout_ms: int) -> InfluxDBClient:
    return InfluxDBClient(
        url=env["INFLUXDB_URL"],
        token=env["INFLUXDB_TOKEN"],
        org=env["INFLUXDB_ORG"],
        timeout=timeout_ms,
    )


def _interrupt(signum, frame) -> None:
    raise KeyboardInterrupt(f"signal {signum}")


def main(argv: Optional[list] = None) -> int:
    args = parse_args(argv)
    # A closed terminal or a kill still runs the rollback of the renames
    for name in ("SIGTERM", "SIGHUP", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _interrupt)
    env = load_dotenv_values()
    # Long queries and writes; short bucket calls, so a hung rename fails fast
    client, admin = _client(env, QUERY_TIMEOUT_MS), _client(env, BUCKET_TIMEOUT_MS)
    start = datetime.datetime.combine(args.start, datetime.time(), tzinfo=UTC)
    try:
        if args.step == "copy":
            return run_copy(client, admin.buckets_api(), args.bucket, start, args.dry_run)
        if args.step == "cutover":
            live = args.writers_running
            return run_cutover(client, admin.buckets_api(), args.bucket, start, live)
        return run_verify(client, admin.buckets_api(), args.bucket, start)
    finally:
        if args.writers_stopped:
            print(f"Start the writers again: sudo systemctl start {writers(args.bucket)}")
        client.close()
        admin.close()


def load_dotenv_values() -> dict:
    """The InfluxDB settings from .env and the environment."""
    load_dotenv()
    missing = [k for k in ("INFLUXDB_URL", "INFLUXDB_TOKEN", "INFLUXDB_ORG") if k not in os.environ]
    if missing:
        raise SystemExit(f"Not set: {', '.join(missing)}")
    return {k: os.environ[k] for k in ("INFLUXDB_URL", "INFLUXDB_TOKEN", "INFLUXDB_ORG")}


if __name__ == "__main__":
    sys.exit(main())
