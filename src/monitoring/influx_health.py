"""Detect InfluxDB performance degradation before queries start failing.

Thresholds come from measured states of this instance, all on
2026-09-13: degraded after 162 days of uptime, healthy just after a
restart, and healthy again 4 h later.

                    degraded   healthy    4 h in
  GC pause p50       0.257 s  0.00016 s  0.00016 s
  resident           2.62 GB   1.63 GB   1.54 GB
  sys_bytes          2.62 GB   1.63 GB   2.21 GB
  shards               2936      1729      1729
  goroutines          18696     18510     18513

Goroutines barely moved, so they are not the signal despite looking
alarming. Shards drive memory, memory drives GC pauses, and GC pauses
are what time out a write.

Query latency is logged but never warns. Over 75 runs on 2026-09-14
it ran min 0.08 s, median 0.82 s, p90 2.77 s, max 20.47 s while GC
pause, resident and shard count all held steady, and curl on the same
host answered the identical query in 31 ms throughout. It tracks host
timing rather than the database, and the 1.98 s once read on the
degraded instance falls inside that healthy spread, so it never
separated the two states. A probe that fails outright is still a
failure.

Memory is logged and never warns either. A 2.0 GB line on resident
sent a mail every 6 h for a week: at 8.5 days uptime the instance
oscillated 1.69 to 2.22 GB, so the line sat inside normal operation.
The band also widens with uptime, so no fixed line survives. Resident
is still the honest figure to log, because sys_bytes counts pages Go
has already given back and climbs on a healthy instance.

That leaves GC pause and shard count, which separated the two states
by 1600-fold and by 1207 shards. GC pause is the live signal: it
reached 0.0103 s at 8.5 days, 64 times the post-restart value and
still a fifth of its threshold.
"""

import re
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from src.common.logger import setup_logger

logger = setup_logger(__name__, "influx_health.log")

# Each sits between the two measured states, so this warns while there
# is still headroom rather than once queries already fail.
GC_PAUSE_WARN_SECONDS = 0.05
SHARD_COUNT_WARN = 2200

METRICS_TIMEOUT_SECONDS = 30
PROBE_QUERY = "buckets() |> limit(n: 1)"


def _fetch_metrics(url: str, token: str) -> Optional[str]:
    """Return the Prometheus metrics text, or None if unreachable."""
    request = urllib.request.Request(
        f"{url.rstrip('/')}/metrics", headers={"Authorization": f"Token {token}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=METRICS_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        logger.warning("Cannot read InfluxDB metrics: %s", e)
        return None


def _gauge(text: str, name: str) -> Optional[float]:
    """Read a single unlabelled gauge value."""
    match = re.search(rf"^{re.escape(name)}\s+([0-9.e+-]+)$", text, re.M)
    return float(match.group(1)) if match else None


def _gc_pause_median(text: str) -> Optional[float]:
    match = re.search(r'^go_gc_duration_seconds\{quantile="0\.5"\}\s+([0-9.e+-]+)$', text, re.M)
    return float(match.group(1)) if match else None


def _shard_count(text: str) -> int:
    return len(re.findall(r"^storage_tsm_files_total\{", text, re.M))


def _resident_bytes(text: str) -> Optional[float]:
    """Reserved address space minus the pages Go gave back to the OS.

    Released pages stay in sys_bytes but cost nothing, so counting them
    warns on uptime rather than on memory pressure.
    """
    sys_bytes = _gauge(text, "go_memstats_sys_bytes")
    if sys_bytes is None:
        return None
    released = _gauge(text, "go_memstats_heap_released_bytes")
    if released is None:
        released = 0.0
    return sys_bytes - released


def probe_query_seconds(influx: Any) -> Optional[float]:
    """Run a query that scans no data, to prove the instance answers.

    The elapsed time is logged for trend only. See the module docstring
    for why it is too noisy on this host to warn on.
    """
    started = time.perf_counter()
    try:
        influx.query_api.query(PROBE_QUERY)
    except Exception as e:
        logger.error("InfluxDB probe query failed: %s", e)
        return None
    return time.perf_counter() - started


def check_influxdb_performance(influx: Any, url: str, token: str) -> tuple[list[str], list[str]]:
    """Warn when InfluxDB looks like it is heading for timeouts.

    Returns:
        Tuple of (failures, warnings)
    """
    failures: list[str] = []
    warnings: list[str] = []

    elapsed = probe_query_seconds(influx)
    if elapsed is None:
        failures.append("InfluxDB probe query failed")
    else:
        logger.info("InfluxDB probe query: %.3f s", elapsed)

    text = _fetch_metrics(url, token)
    if text is None:
        warnings.append("Cannot read InfluxDB metrics endpoint")
        return failures, warnings

    warnings.extend(metric_warnings(text))
    return failures, warnings


def metric_warnings(text: str) -> list[str]:
    """Warnings from the metrics text, logging the numbers for trend."""
    gc_pause = _gc_pause_median(text)
    resident = _resident_bytes(text)
    shards = _shard_count(text)
    logger.info(
        "InfluxDB gc_p50=%s resident=%s shards=%d",
        f"{gc_pause:.3f}s" if gc_pause is not None else "?",
        f"{resident / 1e9:.2f}GB" if resident is not None else "?",
        shards,
    )

    out: list[str] = []
    if gc_pause is not None and gc_pause > GC_PAUSE_WARN_SECONDS:
        out.append(
            f"InfluxDB GC pauses at {gc_pause:.2f}s median "
            f"(warn above {GC_PAUSE_WARN_SECONDS}s); this is what times out writes"
        )
    if shards > SHARD_COUNT_WARN:
        out.append(
            f"InfluxDB holding {shards} shards (warn above {SHARD_COUNT_WARN}); "
            "widen shard group durations, a restart only clears emptied ones"
        )
    return out
