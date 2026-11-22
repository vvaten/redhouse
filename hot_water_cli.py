#!/usr/bin/env python
"""Manual hot water control: status, boost anchor, circulate and pause.

The CLI never switches the relay. circulate and pause write a request that
the next tick (within a minute) applies. Run it as the unit's user through
hotwater.sh: hotwater status | anchor "2026-11-27 22:00" | circulate 30 |
pause 60 | commission
"""

import argparse
import datetime
import getpass
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import pytz

from src.common.atomic_json import write_json
from src.common.config import get_config
from src.common.local_time import HELSINKI
from src.control import hot_water_boost as hb
from src.control import hot_water_controller as hwc
from src.control.hardware_implementations import HotWaterRelay

REPO = Path(__file__).resolve().parent
ANCHOR_FILE = hb.ANCHOR_FILE
UNIT = REPO / "deployment" / "systemd" / "redhouse-hot-water.service"
RUN_DIR_ENV = "HOT_WATER_RUN_DIR"


def unit_user(unit: Path = UNIT) -> str:
    users = [
        ln.split("=", 1)[1].strip()
        for ln in unit.read_text().splitlines()
        if ln.startswith("User=")
    ]
    if len(users) != 1:
        raise ValueError(f"{unit} has no single User= line")
    return users[0]


def run_dir() -> Path:
    """The tick's RAM folder; staging's install has its own."""
    if RUN_DIR_ENV in os.environ:
        return Path(os.environ[RUN_DIR_ENV])
    name = "redhouse-staging-hot-water" if staging_install() else "redhouse-hot-water"
    return Path("/run") / name


def check_user(expected: str, actual: Optional[str] = None) -> None:
    actual = getpass.getuser() if actual is None else actual
    if actual != expected:
        raise PermissionError(f"Run as {expected} (hotwater.sh does it), not {actual}")


def parse_local(text: str) -> int:
    """A Helsinki wall time such as 2026-11-27 22:00 as UTC epoch seconds."""
    naive = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")
    try:
        return int(HELSINKI.localize(naive, is_dst=None).timestamp())
    except (pytz.AmbiguousTimeError, pytz.NonExistentTimeError) as e:
        raise ValueError(f"{text} is not one clear local time (a DST change): {e}") from e


def staging_install() -> bool:
    return REPO.name.endswith("-staging")


def write_request(folder: Path, action: str, minutes: int, now: float) -> Path:
    if not folder.is_dir():
        raise FileNotFoundError(f"{folder} does not exist: is the hot water timer running?")
    path = folder / hwc.REQUEST_FILE
    write_json(path, {"action": action, "minutes": minutes, "requested_at": int(now)})
    return path


def show_status(settings: dict, folder: Path, now: int) -> None:
    s = settings["boost"]
    last = hb.expiry_at_or_before(now, s)
    nxt = hb.next_expiry(now, s)
    print(f"Anchor:        {hb.format_utc(s.anchor_utc)}")
    if last is not None:
        print(
            f"Last expiry:   {hb.format_utc(last)} (slot to {hb.format_utc(hb.slot_end(last, s))})"
        )
    print(f"Next expiry:   {hb.format_utc(nxt)}")
    print(f"Modes:         {settings['modes']}")
    state = hwc.load_state(folder / hwc.STATE_FILE)
    if state is None:
        print(f"No tick state in {folder}")
        return
    print(f"Last tick:     {time.ctime(state.last_tick)}")
    print(f"Relay memory:  {state.mem}")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Manual hot water control")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("commission", help="Set the relay's power-on state to OFF")
    anchor = sub.add_parser("anchor", help="The minute of the last pump restart, local time")
    anchor.add_argument("when", help='"YYYY-MM-DD HH:MM"')
    for name in ("circulate", "pause"):
        p = sub.add_parser(name)
        p.add_argument("minutes", type=int)
    args = parser.parse_args(argv)
    config = get_config()
    section = config.hot_water_section
    settings = hwc.load_settings(section, staging_install(), hb.read_anchor(ANCHOR_FILE))
    now = int(time.time())
    if args.command == "status":
        show_status(settings, run_dir(), now)
        return 0
    check_user(unit_user())
    if args.command == "anchor":
        epoch = parse_local(args.when)
        if epoch > now:
            print("The anchor is a past restart, not a future one")
            return 1
        hb.write_anchor(ANCHOR_FILE, epoch, now)
        nxt = hb.next_expiry(now, hb.load_boost_settings(section["boost"], epoch))
        print(f"Anchor set to {hb.format_utc(epoch)}; next expiry {hb.format_utc(nxt)}")
        return 0
    if args.command == "commission":
        if staging_install():
            print("Staging has a mock relay: commission only from the production install")
            return 1
        state = HotWaterRelay(str(settings["relay_url"])).default_off()
        print(f"Relay power-on state: {state}")
        return 0 if state == "off" else 1
    if args.minutes < 0:
        print("minutes must be 0 or more")
        return 1
    path = write_request(run_dir(), args.command, args.minutes, now)
    print(f"Request {args.command} {args.minutes} min written to {path}; the next tick applies it")
    print(json.dumps({"action": args.command, "minutes": args.minutes}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (PermissionError, FileNotFoundError, ValueError) as e:
        print(f"[FAILED] {e}")
        sys.exit(1)
