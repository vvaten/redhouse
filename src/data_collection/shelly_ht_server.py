#!/usr/bin/env python
"""Receive Shelly H&T reports over HTTP and keep the latest values in a file.

Each sensor calls GET /?id=<id>&temp=<C>&hum=<%> when its reading changes.
The temperature collector reads the file every minute.
"""

import errno
import json
import os
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from src.common.logger import setup_logger

logger = setup_logger(__name__, "shelly_ht.log")

# The sensors are configured to report to this port on the Pi
PORT = 8008
STATUS_FILE = Path(__file__).resolve().parents[2] / "data" / "shelly_ht_status.json"
# Written by wibatemp's service; read when ours is missing, so a cutover
# keeps the last value of every sensor
LEGACY_STATUS_FILE = Path("/home/pi/wibatemp/temperature_status.json")


def parse_report(path: str, client_ip: str) -> Optional[tuple[str, dict]]:
    """(sensor id, values) of a report, or None when it names no sensor. The
    id ends with the last octet of the sender's address, as wibatemp named it."""
    query = parse_qs(urlparse(path).query)
    if "id" not in query:
        return None
    sensor_id = f"{query['id'][0]}-{client_ip.split('.')[-1]}"
    values = {}
    for key in ("temp", "hum"):
        if key in query:
            try:
                values[key] = float(query[key][0])
            except ValueError:
                logger.warning(f"Ignoring {key}={query[key][0]!r} from {sensor_id}")
    return sensor_id, values


def record(state: dict, sensor_id: str, values: dict, now: float) -> bool:
    """Merge a report into the state. A report without a value changes nothing."""
    if not values:
        return False
    entry = state.setdefault(sensor_id, {"temp": None, "hum": None})
    entry.update(values)
    entry["updated"] = now
    return True


def load_state(status_file: Path = STATUS_FILE, legacy_file: Path = LEGACY_STATUS_FILE) -> dict:
    """The last values of every sensor, from our file or else from wibatemp's."""
    for path in (status_file, legacy_file):
        if not path.exists():
            continue
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            logger.warning(f"Cannot read Shelly HT state from {path}: {e}")
            return {}
        logger.info(f"Loaded {len(state)} Shelly HT sensors from {path}")
        return state
    return {}


def write_state(state: dict, status_file: Path = STATUS_FILE) -> None:
    """Replace the file in one step, so a reader never sees half of it."""
    status_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=status_file.parent, prefix=".shelly_ht_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, status_file)
    except BaseException:
        os.unlink(tmp_name)
        raise


class ReportHandler(BaseHTTPRequestHandler):
    """Records each report. make_handler gives it the state and the file."""

    state: dict
    status_file: Path
    # One request at a time: a sensor that drops off mid-request must not
    # hold the receiver for good
    timeout = 10

    def do_GET(self):
        try:
            report = parse_report(self.path, self.client_address[0])
            if report is not None and record(self.state, *report, time.time()):
                write_state(self.state, self.status_file)
                logger.info(f"Shelly HT {report[0]}: {report[1]}")
            self._reply(200, "ok")
        except Exception as e:
            logger.error(f"Failed to record Shelly HT report {self.path}: {e}")
            self._reply(500, "Internal error, see logs for more details.")

    def _reply(self, status: int, body: str) -> None:
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):
        """Keep the per-request access line out of the journal."""
        logger.debug(format % args)


def make_handler(state: dict, status_file: Path) -> type:
    return type(
        "BoundReportHandler", (ReportHandler,), {"state": state, "status_file": status_file}
    )


def main(port: int = PORT) -> int:
    state = load_state()
    try:
        server = HTTPServer(("", port), make_handler(state, STATUS_FILE))
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            logger.error(f"Port {port} is taken: is wibatemp's Shelly HT receiver still running?")
            return 1
        raise
    logger.info(f"Shelly HT server listening on port {port}, writing {STATUS_FILE}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
