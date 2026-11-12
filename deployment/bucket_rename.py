"""The rename step of swap_bucket_shards.py: swap two buckets' names by ID
at a moment clear of every timer, and always leave a bucket with the name."""

import contextlib
import datetime
import signal
import time
from typing import Optional

import pytz

UTC = datetime.timezone.utc
RENAME_ATTEMPTS = 3
RETRY_PAUSE_S = 5
# Minute 9, 24, 39 or 54 at :45 is clear of every timer that reads or
# writes these buckets, so no reader meets the moment without a bucket X
RENAME_MINUTE_MOD_15 = 9
RENAME_SECOND = 45
MAX_SLOT_WAIT = datetime.timedelta(minutes=5)
TIMING = (
    "Stop the writers just after minute 8, 23, 38 or 53 and wait until their\n"
    "services are inactive. The cutover renames at minute 9, 24, 39 or 54 and\n"
    "second 45; start the writers as soon as it ends, so that no run is skipped."
)
SIGNALS = ("SIGINT", "SIGTERM", "SIGHUP", "SIGBREAK")
# The JSON log files and the journal on the Pi use local time
PI_TIME = pytz.timezone("Europe/Helsinki")
# A temperature run that met the renames logs its failure within this time
JOURNAL_MARGIN = datetime.timedelta(minutes=10)


def say(text: str) -> None:
    """Print, but never fail: after a hangup the terminal is gone, and the
    rollback must still run."""
    try:
        print(text, flush=True)
    except OSError:
        pass


@contextlib.contextmanager
def signals_held():
    """A second Ctrl-C or kill must not stop the rollback halfway."""
    held = [getattr(signal, name) for name in SIGNALS if hasattr(signal, name)]
    previous = [signal.signal(sig, signal.SIG_IGN) for sig in held]
    try:
        yield
    finally:
        for sig, handler in zip(held, previous):
            signal.signal(sig, handler)


def _name_of(buckets_api, bucket_id: str) -> Optional[str]:
    try:
        return buckets_api.find_bucket_by_id(bucket_id).name
    except Exception as e:
        say(f"  Could not read bucket {bucket_id}: {e}")
        return None


def rename(buckets_api, bucket_id: str, name: str) -> bool:
    """Rename by ID, then read the name back by ID: a call that failed on
    the way back may still have renamed. Returns whether the name took."""
    for attempt in range(1, RENAME_ATTEMPTS + 1):
        try:
            bucket = buckets_api.find_bucket_by_id(bucket_id)
            bucket.name = name
            buckets_api.update_bucket(bucket)
        except Exception as e:
            say(f"  Rename to {name}, attempt {attempt}: {e}")
        if _name_of(buckets_api, bucket_id) == name:
            return True
        time.sleep(RETRY_PAUSE_S * attempt)
    return False


def _by_hand(source, target, name: str) -> list:
    return [
        f"  influx bucket update --id {target.id} --name {name}_new",
        f"  influx bucket update --id {source.id} --name {name}",
    ]


def restore_names(buckets_api, source, target, name: str) -> None:
    """Give X its name back; X_new first gives the name up if it took it."""
    with signals_held():
        if _name_of(buckets_api, target.id) == name:
            rename(buckets_api, target.id, f"{name}_new")
        if rename(buckets_api, source.id, name):
            say(f"Rolled back: {name} is the original bucket")
            return
        say(f"ROLLBACK FAILED. Now {source.id} is {_name_of(buckets_api, source.id)}")
        say(f"and {target.id} is {_name_of(buckets_api, target.id)}. By hand on the NAS:")
        say("\n".join(_by_hand(source, target, name)))


def swap_names(buckets_api, source, target) -> bool:
    """X to X_old, then X_new to X; on any failure or interrupt X gets its
    name back, as a rename that seemed to fail may have taken."""
    name = source.name
    say("Renaming. If this stops halfway, restore on the NAS:")
    say("\n".join(_by_hand(source, target, name)))
    try:
        if rename(buckets_api, source.id, f"{name}_old"):
            if rename(buckets_api, target.id, name):
                return True
            say(f"{name}_new could not take the name {name}")
        else:
            say(f"{name} could not be renamed to {name}_old")
    except BaseException:
        say("Interrupted during the renames")
        restore_names(buckets_api, source, target, name)
        raise
    restore_names(buckets_api, source, target, name)
    return False


def next_rename_time(now: datetime.datetime) -> datetime.datetime:
    """The next moment at minute 9, 24, 39 or 54 and second 45."""
    hour = now.replace(minute=0, second=0, microsecond=0)
    for quarter in range(0, 75, 15):
        moment = hour + datetime.timedelta(
            minutes=quarter + RENAME_MINUTE_MOD_15, seconds=RENAME_SECOND
        )
        if moment > now:
            return moment
    raise AssertionError("unreachable: the next hour always has a moment")


def wait_for_slot() -> bool:
    """Wait for the rename moment, unless the stopped writers would miss a run."""
    slot = next_rename_time(datetime.datetime.now(UTC))
    if slot - datetime.datetime.now(UTC) > MAX_SLOT_WAIT:
        print(f"The rename moment {slot} is too far away; nothing renamed.")
        print(TIMING)
        return False
    print(f"Renaming at {slot}")
    while datetime.datetime.now(UTC) < slot:
        time.sleep(min(1.0, (slot - datetime.datetime.now(UTC)).total_seconds()))
    return True


def replay_minutes(first: datetime.datetime, last: datetime.datetime) -> list:
    """The minutes, in Pi time, whose temperature write may have met the renames."""
    minute = (first - datetime.timedelta(minutes=1)).replace(second=0, microsecond=0)
    minutes = []
    while minute <= last:
        minutes.append(minute.astimezone(PI_TIME).strftime("%Y%m%d_%H%M"))
        minute += datetime.timedelta(minutes=1)
    return minutes


def print_replay_hint(first: datetime.datetime, last: datetime.datetime) -> None:
    """A write that met the renames failed, but the JSON log kept the reading.
    A replay adds a second point next to a write that went through."""
    minutes = replay_minutes(first, last)
    since = (first - datetime.timedelta(minutes=1)).astimezone(PI_TIME)
    until = (last + JOURNAL_MARGIN).astimezone(PI_TIME)
    say(f"Temperature writes in the minutes {', '.join(minutes)} (Pi time) may have")
    say("failed. Each failure follows the JSON log file of its run; on the Pi:")
    say(
        f"  journalctl -u redhouse-temperature.service --since '{since:%Y-%m-%d %H:%M}'"
        f" --until '{until:%Y-%m-%d %H:%M}'"
        " | grep -E 'Logged data to|Failed to write temperatures'"
    )
    say("Replay only the minute M (YYYYMMDD_HHMM) of a file whose write failed:")
    say(
        "  cd /opt/redhouse && venv/bin/python -m src.tools.replay_json_logs"
        " --source temperature --start M --stop M --dry-run"
    )
