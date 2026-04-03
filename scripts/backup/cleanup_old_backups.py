#!/usr/bin/env python3
"""
Clean up old backup snapshots based on retention policy.

Retention policy:
  - Keep all backups from the last 30 days
  - For backups older than 30 days, keep one per week (the newest) up to 114 days
  - Delete everything older than 114 days
  - This yields ~30 daily + ~12 weekly = ~42 recovery points

Can run on the Pi (via SSH to list/delete on NAS) or directly on the NAS.

Usage:
    python -u scripts/backup/cleanup_old_backups.py /path/to/backups/
    python -u scripts/backup/cleanup_old_backups.py /path/to/backups/ --dry-run
"""

import argparse
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

DAILY_RETENTION_DAYS = 30
WEEKLY_RETENTION_DAYS = 114  # 30 daily + 12 weeks = ~42 recovery points

# Snapshot directories are named YYYY-MM-DD_HHMMSS
SNAPSHOT_FORMAT = "%Y-%m-%d_%H%M%S"


def parse_snapshot_date(name: str) -> Optional[datetime]:
    """Parse a snapshot directory name into a datetime, or None if invalid."""
    try:
        return datetime.strptime(name, SNAPSHOT_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _iso_week_key(dt: datetime) -> str:
    """Return ISO year-week string for grouping (e.g. '2026-W14')."""
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def find_snapshots_to_delete(
    backup_root: Path,
    now: Optional[datetime] = None,
) -> tuple[list[Path], list[Path]]:
    """Determine which snapshot directories to delete.

    Args:
        backup_root: Directory containing snapshot subdirectories
        now: Current time (for testing; defaults to utcnow)

    Returns:
        Tuple of (to_delete, to_keep) lists of Path objects
    """
    if now is None:
        now = datetime.now(timezone.utc)

    daily_cutoff = now - timedelta(days=DAILY_RETENTION_DAYS)
    weekly_cutoff = now - timedelta(days=WEEKLY_RETENTION_DAYS)

    # Collect all valid snapshot dirs
    snapshots: list[tuple[datetime, Path]] = []
    for entry in sorted(backup_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name == "latest":
            continue
        dt = parse_snapshot_date(entry.name)
        if dt is not None:
            snapshots.append((dt, entry))

    to_keep: list[Path] = []
    to_delete: list[Path] = []

    # Group older-than-30-days snapshots by ISO week
    weekly_groups: dict[str, list[tuple[datetime, Path]]] = defaultdict(list)

    for dt, path in snapshots:
        if dt >= daily_cutoff:
            # Within 30 days -- keep all
            to_keep.append(path)
        elif dt >= weekly_cutoff:
            # 30-114 days -- group by week, keep newest per week
            week_key = _iso_week_key(dt)
            weekly_groups[week_key].append((dt, path))
        else:
            # Older than 114 days -- delete
            to_delete.append(path)

    # For each week group, keep the newest, delete the rest
    for _week_key, group in weekly_groups.items():
        group.sort(key=lambda x: x[0], reverse=True)
        to_keep.append(group[0][1])
        for _, path in group[1:]:
            to_delete.append(path)

    return to_delete, to_keep


def cleanup_old_backups(
    backup_root: Path,
    dry_run: bool = False,
) -> int:
    """Remove old backup snapshots per retention policy.

    Args:
        backup_root: Directory containing snapshot subdirectories
        dry_run: If True, print what would be deleted without deleting

    Returns:
        Number of snapshots deleted (or that would be deleted in dry-run)
    """
    to_delete, to_keep = find_snapshots_to_delete(backup_root)

    if not to_delete:
        print(f"No backups to clean up ({len(to_keep)} kept)")
        return 0

    action = "Would delete" if dry_run else "Deleting"
    for path in sorted(to_delete):
        print(f"  {action}: {path.name}")
        if not dry_run:
            shutil.rmtree(str(path), ignore_errors=True)

    suffix = " (dry-run)" if dry_run else ""
    print(f"Cleanup: {len(to_delete)} deleted, {len(to_keep)} kept{suffix}")
    return len(to_delete)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Clean up old backup snapshots",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("backup_root", type=Path, help="Directory containing backup snapshots")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted")
    args = parser.parse_args()

    if not args.backup_root.is_dir():
        print(f"ERROR: Not a directory: {args.backup_root}", file=sys.stderr)
        return 1

    cleanup_old_backups(args.backup_root, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
