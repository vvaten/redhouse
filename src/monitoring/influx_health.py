"""Detect InfluxDB performance degradation before queries start failing.

Thresholds come from two measured states of this instance on
2026-09-13, degraded after 162 days of uptime and healthy after a
restart:

                    degraded   healthy
  query latency       1.98 s    0.03 s
  GC pause p50       0.257 s  0.00016 s
  sys_bytes          2.62 GB   1.63 GB
  shards               2936      1729
  goroutines          18696     18510

Goroutines barely moved, so they are not the signal despite looking
alarming. Shards drive memory, memory drives GC pauses, and GC pauses
are what time out a write.
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
QUERY_LATENCY_WARN_SECONDS = 1.0
GC_PAUSE_WARN_SECONDS = 0.05
SYS_BYTES_WARN = 2_000_000_000
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


def probe_query_seconds(influx: Any) -> Optional[float]:
    """Time a query that scans no data, so this measures overhead only."""
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
        if elapsed > QUERY_LATENCY_WARN_SECONDS:
            warnings.append(
                f"InfluxDB slow: probe query took {elapsed:.2f}s "
                f"(warn above {QUERY_LATENCY_WARN_SECONDS}s, healthy is under 0.1s)"
            )

    text = _fetch_metrics(url, token)
    if text is None:
        warnings.append("Cannot read InfluxDB metrics endpoint")
        return failures, warnings

    warnings.extend(metric_warnings(text))
    return failures, warnings


def metric_warnings(text: str) -> list[str]:
    """Warnings from the metrics text, logging the numbers for trend."""
    gc_pause = _gc_pause_median(text)
    sys_bytes = _gauge(text, "go_memstats_sys_bytes")
    shards = _shard_count(text)
    logger.info(
        "InfluxDB gc_p50=%s sys_bytes=%s shards=%d",
        f"{gc_pause:.3f}s" if gc_pause is not None else "?",
        f"{sys_bytes / 1e9:.2f}GB" if sys_bytes is not None else "?",
        shards,
    )

    out: list[str] = []
    if gc_pause is not None and gc_pause > GC_PAUSE_WARN_SECONDS:
        out.append(
            f"InfluxDB GC pauses at {gc_pause:.2f}s median "
            f"(warn above {GC_PAUSE_WARN_SECONDS}s); this is what times out writes"
        )
    if sys_bytes is not None and sys_bytes > SYS_BYTES_WARN:
        out.append(
            f"InfluxDB reserving {sys_bytes / 1e9:.2f} GB "
            f"(warn above {SYS_BYTES_WARN / 1e9:.1f} GB); a restart reclaims it"
        )
    if shards > SHARD_COUNT_WARN:
        out.append(
            f"InfluxDB holding {shards} shards (warn above {SHARD_COUNT_WARN}); "
            "widen shard group durations, a restart only clears emptied ones"
        )
    return out
