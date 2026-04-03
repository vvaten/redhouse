#!/usr/bin/env python3
"""
Back up critical Raspberry Pi files to the NAS via rsync.

Copies .env, config.yaml, pump_state.json, and systemd units to a local
staging directory, verifies the snapshot, then rsyncs to the NAS.
Runs cleanup of old snapshots on NAS and sends an email alert on failure.

Usage:
    python -u scripts/backup/run_backup_pi.py
    python -u scripts/backup/run_backup_pi.py --dry-run
    python -u scripts/backup/run_backup_pi.py --verbose
"""

import argparse
import json
import logging
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.backup.backup_pi_files import backup_pi_files
from scripts.backup.cleanup_old_backups import (
    find_snapshots_to_delete,
    parse_snapshot_date,
)
from scripts.backup.verify_backup_pi import verify_backup
from src.common.config import Config, get_config
from src.common.logger import setup_logger
from src.monitoring.email_sender import format_alert_body, send_alert_email

logger = setup_logger(__name__, "backup.log")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Back up Pi files to NAS via rsync",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Stage files locally but skip rsync to NAS",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def _rsync_to_nas(
    local_dir: Path,
    nas_host: str,
    nas_user: str,
    nas_ssh_key: str,
    nas_path: str,
    snapshot_name: str,
) -> tuple[bool, str]:
    """Rsync a local directory to the NAS.

    Args:
        local_dir: Local staging directory to sync
        nas_host: NAS hostname or IP
        nas_user: NAS SSH user
        nas_ssh_key: Path to SSH private key
        nas_path: Base backup path on NAS
        snapshot_name: Dated directory name for this snapshot

    Returns:
        Tuple of (success, error_message)
    """
    dest = f"{nas_user}@{nas_host}:{nas_path}/{snapshot_name}/"
    cmd = [
        "rsync",
        "-avz",
        "--timeout=60",
        "-e",
        f"ssh -i {nas_ssh_key} -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15",
        f"{local_dir}/",
        dest,
    ]

    logger.info("Rsyncing to %s", dest)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return False, f"rsync failed (exit {result.returncode}): {result.stderr.strip()}"
        logger.info("Rsync complete")
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "rsync timed out after 120 seconds"
    except FileNotFoundError:
        return False, "rsync command not found"


def _update_latest_symlink(
    nas_host: str,
    nas_user: str,
    nas_ssh_key: str,
    nas_path: str,
    snapshot_name: str,
) -> bool:
    """Update the 'latest' symlink on the NAS to point to the new snapshot."""
    ssh_cmd = [
        "ssh",
        "-i",
        nas_ssh_key,
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=15",
        f"{nas_user}@{nas_host}",
        f"cd {nas_path} && rm -f latest && ln -s {snapshot_name} latest",
    ]

    try:
        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.error("Failed to update latest symlink: %s", result.stderr.strip())
            return False
        logger.info("Updated latest symlink -> %s", snapshot_name)
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.error("Failed to update latest symlink: %s", e)
        return False


def _run_remote_cleanup(
    nas_host: str,
    nas_user: str,
    nas_ssh_key: str,
    nas_path: str,
) -> bool:
    """Run cleanup of old backup snapshots on the NAS via SSH.

    Lists snapshot directories remotely and removes old ones per retention policy.
    """
    ssh_cmd = [
        "ssh",
        "-i",
        nas_ssh_key,
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=15",
        f"{nas_user}@{nas_host}",
        f"ls -1 {nas_path}/",
    ]

    try:
        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning("Cannot list NAS backup dirs for cleanup: %s", result.stderr.strip())
            return False

        # Create a temporary local dir mirroring the NAS structure (dirs only)
        # so cleanup_old_backups can determine what to delete
        tmp_mirror = Path(tempfile.mkdtemp(prefix="redhouse-cleanup-"))
        try:
            for name in result.stdout.strip().split("\n"):
                name = name.strip()
                if name and name != "latest":
                    (tmp_mirror / name).mkdir(exist_ok=True)

            to_delete, _to_keep = find_snapshots_to_delete(tmp_mirror)

            # Delete the identified snapshots on NAS
            for path in to_delete:
                if parse_snapshot_date(path.name) is None:
                    logger.error("Refusing to delete non-snapshot dir: %s", path.name)
                    continue
                rm_cmd = [
                    "ssh",
                    "-i",
                    nas_ssh_key,
                    "-o",
                    "ConnectTimeout=15",
                    f"{nas_user}@{nas_host}",
                    f"rm -rf {nas_path}/{path.name}",
                ]
                rm_result = subprocess.run(
                    rm_cmd, capture_output=True, text=True, timeout=30
                )
                if rm_result.returncode == 0:
                    logger.info("Cleaned up old snapshot: %s", path.name)
                else:
                    logger.warning("Failed to delete %s: %s", path.name, rm_result.stderr.strip())

            if to_delete:
                print(f"  Cleaned up {len(to_delete)} old snapshot(s) on NAS")
            return True
        finally:
            shutil.rmtree(str(tmp_mirror), ignore_errors=True)

    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("Remote cleanup failed: %s", e)
        return False


def _send_failure_alert(config: Config, failures: list[str]) -> None:
    """Send alert email on backup failure."""
    resend_api_key = config.get("RESEND_API_KEY")
    alert_email_to = config.get("ALERT_EMAIL_TO")
    alert_email_from = config.get("ALERT_EMAIL_FROM")

    if not resend_api_key or not alert_email_to:
        logger.error("Alert email not configured (RESEND_API_KEY / ALERT_EMAIL_TO)")
        return

    if not alert_email_from:
        logger.error("ALERT_EMAIL_FROM not configured in .env")
        return

    hostname = platform.node()
    subject = f"[RedHouse FAILURE] {hostname}: Pi backup failed"
    body = format_alert_body(hostname, failures)

    send_alert_email(
        api_key=resend_api_key,
        to_email=alert_email_to,
        subject=subject,
        body=body,
        from_email=alert_email_from,
    )


def main() -> int:
    """Main entry point."""
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    config = get_config()
    project_root = Path("/opt/redhouse")
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d_%H%M%S")

    # Read NAS config
    nas_host = config.get("BACKUP_NAS_HOST")
    nas_user = config.get("BACKUP_NAS_USER")
    nas_ssh_key = config.get("BACKUP_NAS_SSH_KEY")
    nas_path = config.get("BACKUP_NAS_PATH")

    if not nas_host:
        print("ERROR: BACKUP_NAS_HOST not configured in .env", file=sys.stderr)
        return 1
    if not nas_user:
        print("ERROR: BACKUP_NAS_USER not configured in .env", file=sys.stderr)
        return 1
    if not nas_ssh_key:
        print("ERROR: BACKUP_NAS_SSH_KEY not configured in .env", file=sys.stderr)
        return 1
    if not nas_path:
        print("ERROR: BACKUP_NAS_PATH not configured in .env", file=sys.stderr)
        return 1

    # Stage backup to temp directory
    staging_dir = Path(tempfile.mkdtemp(prefix="redhouse-backup-"))
    failures: list[str] = []

    try:
        print(f"Backing up Pi files [{timestamp}]")
        logger.info("Starting Pi backup to staging dir %s", staging_dir)

        # Step 1: Copy files to staging
        backup_result = backup_pi_files(project_root, staging_dir)

        if not backup_result.success:
            failures.extend(backup_result.errors)

        if backup_result.errors:
            for err in backup_result.errors:
                print(f"  WARNING: {err}")

        print(f"  Staged {len(backup_result.files)} files")

        # Step 2: Write manifest
        manifest = {
            "timestamp": now.isoformat(),
            "hostname": platform.node(),
            "snapshot": timestamp,
            "files": backup_result.files,
            "errors": backup_result.errors,
        }
        manifest_path = staging_dir / "backup_manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        # Step 3: Verify staging copy
        verify_failures, verify_warnings = verify_backup(staging_dir)
        for w in verify_warnings:
            print(f"  VERIFY WARNING: {w}")
        if verify_failures:
            for vf in verify_failures:
                print(f"  VERIFY FAILED: {vf}")
            failures.extend(verify_failures)

        # Abort if file copy or verification failed -- no point pushing bad data
        if failures:
            print(f"  ABORTING: {len(failures)} error(s) before rsync")
            _send_failure_alert(config, failures)
            return 1

        # Step 4: Rsync to NAS
        if args.dry_run:
            print("  DRY-RUN: skipping rsync to NAS")
            print(f"  Staged files in {staging_dir}")
            return 0

        rsync_ok, rsync_err = _rsync_to_nas(
            staging_dir,
            nas_host,
            nas_user,
            nas_ssh_key,
            nas_path,
            timestamp,
        )
        if not rsync_ok:
            failures.append(rsync_err)
            print(f"  ERROR: {rsync_err}")
        else:
            print(f"  Synced to {nas_host}:{nas_path}/{timestamp}/")

            # Step 5: Update latest symlink
            if not _update_latest_symlink(
                nas_host, nas_user, nas_ssh_key, nas_path, timestamp
            ):
                failures.append("Failed to update latest symlink on NAS")

            # Step 6: Clean up old snapshots on NAS
            _run_remote_cleanup(nas_host, nas_user, nas_ssh_key, nas_path)

        # Report result
        if failures:
            print(f"  FAILED with {len(failures)} error(s)")
            _send_failure_alert(config, failures)
            return 1

        print("  Backup complete")
        return 0

    except Exception as e:
        msg = f"Backup crashed: {e}"
        logger.exception(msg)
        failures.append(msg)
        _send_failure_alert(config, failures)
        return 1

    finally:
        # Clean up staging dir
        if staging_dir.exists():
            shutil.rmtree(str(staging_dir), ignore_errors=True)
            logger.debug("Cleaned up staging dir %s", staging_dir)


if __name__ == "__main__":
    sys.exit(main())
