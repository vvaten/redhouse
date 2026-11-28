#!/usr/bin/env python
"""Hot water tick - wrapper script for systemd, every minute.

Logs each hot water cycle, follows the legionella boost timer, mails the
alerts and drives the circulation relay by the rule modes of config.yaml.
Staging (its flag or its _staging bucket) uses a mock relay and sends no mail.

Usage, by hand with the timer's RAM state:
    sudo -u pi RUNTIME_DIRECTORY=/run/redhouse-hot-water venv/bin/python run_hot_water.py
"""

import json
import os
import sys
import time
from pathlib import Path

from src.common.atomic_json import write_json
from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger
from src.common.watchdog import Watchdog
from src.control import hot_water_boost as hb
from src.control import hot_water_controller as hwc
from src.control import hot_water_io as hio
from src.control.hardware_implementations import HotWaterRelay, MockHotWaterRelay
from src.control.pump_restore import clock_synchronized
from src.monitoring.alert_mail import is_staging, mailer

logger = setup_logger(__name__, "hot_water.log")

REPO = Path(__file__).resolve().parent
ANCHOR_FILE = hb.ANCHOR_FILE
MOCK_FILE = "mock_relay.json"
INFLUX_TIMEOUT_MS = 8_000
# One short try; the next minute tries again
RELAY_TIMEOUT_S = 3.0
# Under the unit's TimeoutStartSec of 50 s, so the log names the stuck stage
DEADLINE_S = 40


def run_dir() -> Path:
    """systemd's RuntimeDirectory, in RAM; outside systemd the data folder."""
    if "RUNTIME_DIRECTORY" in os.environ:
        return Path(os.environ["RUNTIME_DIRECTORY"])
    return REPO / "data"


def boot_time() -> float:
    """The boot time from /proc/stat, or 0 where there is none."""
    try:
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except OSError:
        pass
    return 0.0


def _guarded(name: str, call, failed):
    def wrapped(*args):
        try:
            return call(*args)
        except Exception as e:
            logger.error(f"Hot water {name} failed: {e}")
            return failed

    return wrapped


def make_io(config, settings: dict, staging: bool, watchdog: Watchdog) -> hwc.TickIO:
    influx = InfluxClient(config, timeout_ms=INFLUX_TIMEOUT_MS)

    def write(points: list) -> None:
        watchdog.mark("InfluxDB write")
        if points:
            # One try: a retry would not fit the deadline, and the next minute writes anew
            influx.write_api.write(bucket=config.influxdb_bucket_load_control, record=points)

    def minutes(start: int, stop: int) -> list:
        watchdog.mark("minute data")
        return hio.read_minutes(influx, config, settings, start, stop)

    return hwc.TickIO(
        read_minutes=_guarded("minute read", minutes, []),
        read_history=_guarded(
            "history read",
            lambda since: hio.read_history(influx, config, since, int(time.time())),
            None,
        ),
        read_prices=_guarded("price read", lambda a, b: hio.read_prices(influx, config, a, b), {}),
        read_program=_guarded("program read", hio.program_entries, []),
        write=_guarded("point write", write, None),
        mail=_guarded("mail", mailer(config, staging), False),
        clock_ok=_guarded("clock check", clock_synchronized, False),
        boot_time=boot_time,
    )


def load(config, staging: bool) -> dict:
    return hwc.load_settings(config.hot_water_section, staging, hb.read_anchor(ANCHOR_FILE))


def mock_relay(url: str, folder: Path, now: float) -> MockHotWaterRelay:
    """Staging's relay, its state kept in RAM so the timer runs out as on the device."""
    try:
        saved = json.loads((folder / MOCK_FILE).read_text())
        on = bool(saved["ison"]) and now < float(saved["off_at"])
    except (OSError, ValueError, KeyError, TypeError):
        on = False
    return MockHotWaterRelay(url, ison=on, has_timer=on)


def save_mock(relay: MockHotWaterRelay, folder: Path, now: float, timer_s: int) -> None:
    if not relay.commands:
        return
    write_json(folder / MOCK_FILE, {"ison": relay.ison, "off_at": now + timer_s})


def main(watchdog: Watchdog) -> int:
    watchdog.mark("config")
    config = get_config()
    staging = is_staging(config)
    settings = load(config, staging)
    url = str(settings["relay_url"])
    now = time.time()
    folder = run_dir()
    relay = (
        mock_relay(url, folder, now)
        if staging
        else HotWaterRelay(url, tries=1, timeout_s=RELAY_TIMEOUT_S)
    )
    io = make_io(config, settings, staging, watchdog)
    watchdog.mark("tick")
    result = hwc.run_once(int(now), settings, relay, io, folder)
    if isinstance(relay, MockHotWaterRelay):
        save_mock(relay, folder, now, settings["limits"].on_timer_s)
    logger.info(
        f"Hot water: top {result.context.top} bottom {result.context.bottom}, "
        f"{result.decision.rule}: {result.action.command} ({result.action.reason}), "
        f"would {result.decision.would}, relay {result.after or result.before}"
    )
    if result.before is None:
        return 1
    if result.action.command != "none" and result.after is None:
        return 1
    return 0


def run() -> int:
    """main under a deadline, so a stuck run logs its stage before systemd kills it."""
    watchdog = Watchdog(DEADLINE_S, log=logger).start()
    try:
        return main(watchdog)
    finally:
        watchdog.cancel()


if __name__ == "__main__":
    sys.exit(run())
