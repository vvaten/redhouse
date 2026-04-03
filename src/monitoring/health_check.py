"""Pi-side health check: disk, services, NAS reachability, backup freshness."""

import json
import platform
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from src.common.config import get_config
from src.common.logger import setup_logger
from src.monitoring.email_sender import format_alert_body, send_alert_email

logger = setup_logger(__name__, "health_check.log")

DISK_WARNING_PERCENT = 80
DISK_CRITICAL_PERCENT = 90

REDHOUSE_SERVICES = [
    "redhouse-temperature",
    "redhouse-weather",
    "redhouse-spot-prices",
    "redhouse-checkwatt",
    "redhouse-shelly-em3",
    "redhouse-windpower",
    "redhouse-aggregate-emeters-5min",
    "redhouse-aggregate-analytics-15min",
    "redhouse-aggregate-analytics-1hour",
    "redhouse-solar-prediction",
    "redhouse-generate-program",
    "redhouse-execute-program",
]


def check_disk_space() -> tuple[list[str], list[str]]:
    """Check disk space on root partition.

    Returns:
        Tuple of (failures, warnings)
    """
    failures: list[str] = []
    warnings: list[str] = []

    try:
        usage = shutil.disk_usage("/")
        percent_used = (usage.used / usage.total) * 100
        free_mb = usage.free / (1024 * 1024)

        if percent_used >= DISK_CRITICAL_PERCENT:
            failures.append(
                f"Disk space critical: {percent_used:.1f}% used " f"({free_mb:.0f} MB free)"
            )
        elif percent_used >= DISK_WARNING_PERCENT:
            warnings.append(
                f"Disk space warning: {percent_used:.1f}% used " f"({free_mb:.0f} MB free)"
            )
        else:
            logger.info("Disk OK: %.1f%% used (%.0f MB free)", percent_used, free_mb)
    except OSError as e:
        failures.append(f"Cannot check disk space: {e}")

    return failures, warnings


def check_systemd_services() -> tuple[list[str], list[str]]:
    """Check that all redhouse systemd timers are active.

    Returns:
        Tuple of (failures, warnings)
    """
    failures: list[str] = []
    warnings: list[str] = []

    if platform.system() != "Linux":
        logger.info("Skipping systemd check on non-Linux platform")
        return failures, warnings

    for service in REDHOUSE_SERVICES:
        timer_name = f"{service}.timer"
        try:
            result = subprocess.run(
                ["systemctl", "is-active", timer_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            status = result.stdout.strip()
            if status != "active":
                failures.append(f"Timer {timer_name} is {status}")
            else:
                logger.debug("Timer %s is active", timer_name)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            warnings.append(f"Cannot check timer {timer_name}: {e}")

    if not failures:
        logger.info("All %d systemd timers are active", len(REDHOUSE_SERVICES))

    return failures, warnings


def check_url_reachable(url: str, name: str, timeout: int = 10) -> Optional[str]:
    """Check if a URL is reachable.

    Args:
        url: URL to check
        name: Human-readable name for logging
        timeout: Request timeout in seconds

    Returns:
        Error message if unreachable, None if OK
    """
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                logger.info("%s reachable at %s", name, url)
                return None
            return f"{name} returned HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return f"{name} returned HTTP {e.code}"
    except (urllib.error.URLError, OSError, socket.timeout) as e:
        return f"{name} unreachable at {url}: {e}"


def check_nas_reachability() -> tuple[list[str], list[str]]:
    """Check that NAS services (InfluxDB, Grafana) are reachable.

    Returns:
        Tuple of (failures, warnings)
    """
    failures: list[str] = []
    warnings: list[str] = []
    config = get_config()

    influx_url = config.influxdb_url
    influx_health = f"{influx_url}/health"
    error = check_url_reachable(influx_health, "InfluxDB")
    if error:
        failures.append(error)

    grafana_url = config.get("GRAFANA_URL")
    if grafana_url:
        grafana_health = f"{grafana_url}/api/health"
        error = check_url_reachable(grafana_health, "Grafana")
        if error:
            warnings.append(error)
    else:
        logger.info("GRAFANA_URL not configured, skipping Grafana check")

    return failures, warnings


def _check_manifest_freshness(
    nas_host: str,
    nas_user: str,
    nas_ssh_key: str,
    manifest_path: str,
    label: str,
) -> tuple[list[str], list[str]]:
    """Check a single backup manifest's freshness via SSH.

    Args:
        nas_host: NAS hostname or IP
        nas_user: NAS SSH user
        nas_ssh_key: Path to SSH private key
        manifest_path: Full path to backup_manifest.json on NAS
        label: Human-readable label (e.g. "Pi backup", "NAS backup")

    Returns:
        Tuple of (failures, warnings)
    """
    failures: list[str] = []
    warnings: list[str] = []

    try:
        result = subprocess.run(
            [
                "ssh",
                "-i",
                nas_ssh_key,
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "ConnectTimeout=10",
                f"{nas_user}@{nas_host}",
                f"cat {manifest_path}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            failures.append(f"Cannot read {label} manifest from NAS: {result.stderr.strip()}")
            return failures, warnings

        manifest = json.loads(result.stdout)
        timestamp_str = manifest.get("timestamp")
        if not timestamp_str:
            failures.append(f"{label} manifest has no timestamp field")
            return failures, warnings

        backup_time = datetime.fromisoformat(timestamp_str)
        age_hours = (datetime.now(timezone.utc) - backup_time).total_seconds() / 3600

        if age_hours > 48:
            failures.append(f"{label} is {age_hours:.0f}h old (last: {timestamp_str})")
        elif age_hours > 26:
            warnings.append(f"{label} is {age_hours:.0f}h old (last: {timestamp_str})")
        else:
            logger.info("%s OK: %.0fh old", label, age_hours)

    except subprocess.TimeoutExpired:
        warnings.append(f"SSH to NAS timed out checking {label} freshness")
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        failures.append(f"Cannot parse {label} manifest: {e}")
    except FileNotFoundError:
        warnings.append(f"SSH not available, cannot check {label} freshness")

    return failures, warnings


def check_backup_freshness() -> tuple[list[str], list[str]]:
    """Check that Pi and NAS backups are less than 48 hours old.

    Uses SSH to read backup manifest timestamps on the NAS.

    Returns:
        Tuple of (failures, warnings)
    """
    failures: list[str] = []
    warnings: list[str] = []
    config = get_config()

    nas_host = config.get("BACKUP_NAS_HOST")
    nas_user = config.get("BACKUP_NAS_USER")
    nas_ssh_key = config.get("BACKUP_NAS_SSH_KEY")
    nas_path = config.get("BACKUP_NAS_PATH")

    if not all([nas_host, nas_user, nas_ssh_key, nas_path]):
        logger.info("Backup config not set, skipping backup freshness check")
        return failures, warnings

    # Check Pi backup freshness
    pi_f, pi_w = _check_manifest_freshness(
        nas_host,
        nas_user,
        nas_ssh_key,
        f"{nas_path}/latest/backup_manifest.json",
        "Pi backup",
    )
    failures.extend(pi_f)
    warnings.extend(pi_w)

    # Check NAS backup freshness
    nas_backup_path = config.get("BACKUP_NAS_LOCAL_PATH")
    if nas_backup_path:
        nas_f, nas_w = _check_manifest_freshness(
            nas_host,
            nas_user,
            nas_ssh_key,
            f"{nas_backup_path}/latest/backup_manifest.json",
            "NAS backup",
        )
        failures.extend(nas_f)
        warnings.extend(nas_w)

    return failures, warnings


def run_health_check() -> int:
    """Run all health checks and send alert email if problems found.

    Returns:
        0 if all checks pass, 1 if there are failures, 2 if warnings only
    """
    config = get_config()
    hostname = platform.node()

    all_failures: list[str] = []
    all_warnings: list[str] = []

    checks = [
        ("disk space", check_disk_space),
        ("systemd services", check_systemd_services),
        ("NAS reachability", check_nas_reachability),
        ("backup freshness", check_backup_freshness),
    ]

    for check_name, check_fn in checks:
        logger.info("Checking %s...", check_name)
        try:
            failures, warnings = check_fn()
            all_failures.extend(failures)
            all_warnings.extend(warnings)
        except Exception as e:
            all_failures.append(f"Check '{check_name}' crashed: {e}")
            logger.exception("Check '%s' raised an exception", check_name)

    if all_failures or all_warnings:
        logger.warning(
            "Health check found %d failures, %d warnings",
            len(all_failures),
            len(all_warnings),
        )

        resend_api_key = config.get("RESEND_API_KEY")
        alert_email_to = config.get("ALERT_EMAIL_TO")
        alert_email_from = config.get("ALERT_EMAIL_FROM", "RedHouse <alerts@resend.dev>")

        if not resend_api_key:
            logger.error("RESEND_API_KEY not configured, cannot send alert")
            return 1 if all_failures else 2

        if not alert_email_to:
            logger.error("ALERT_EMAIL_TO not configured, cannot send alert")
            return 1 if all_failures else 2

        severity = "FAILURE" if all_failures else "WARNING"
        subject = f"[RedHouse {severity}] {hostname}: health check alert"
        body = format_alert_body(hostname, all_failures, all_warnings)

        email_sent = send_alert_email(
            api_key=resend_api_key,
            to_email=alert_email_to,
            subject=subject,
            body=body,
            from_email=alert_email_from,
        )
        if not email_sent:
            logger.error("Failed to send alert email to %s", alert_email_to)
    else:
        logger.info("All health checks passed")

    if all_failures:
        return 1
    if all_warnings:
        return 2
    return 0


def main() -> int:
    """Entry point for health check."""
    logger.info("Starting health check")
    try:
        return run_health_check()
    except Exception as e:
        logger.exception("Health check failed with unexpected error: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
