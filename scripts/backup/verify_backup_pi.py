#!/usr/bin/env python3
"""
Verify integrity of a Pi backup snapshot.

Reads the backup_manifest.json and verifies SHA-256 hashes of all files.
Can be run standalone against any backup directory.

Usage:
    python -u scripts/backup/verify_backup_pi.py /path/to/backup/
    python -u scripts/backup/verify_backup_pi.py /path/to/backup/ --verbose
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.backup.backup_pi_files import sha256_file


def verify_backup(backup_dir: Path, verbose: bool = False) -> tuple[list[str], list[str]]:
    """Verify backup integrity by checking SHA-256 hashes against manifest.

    Args:
        backup_dir: Path to backup snapshot directory
        verbose: Print details for passing checks too

    Returns:
        Tuple of (failures, warnings)
    """
    failures: list[str] = []
    warnings: list[str] = []

    manifest_path = backup_dir / "backup_manifest.json"
    if not manifest_path.exists():
        failures.append(f"Manifest not found: {manifest_path}")
        return failures, warnings

    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        failures.append(f"Cannot read manifest: {e}")
        return failures, warnings

    if "files" not in manifest:
        failures.append("Manifest missing 'files' key")
        return failures, warnings
    files = manifest["files"]
    if not files:
        failures.append("Manifest 'files' is empty")
        return failures, warnings

    checked = 0
    for rel_path, meta in files.items():
        file_path = backup_dir / rel_path
        if not file_path.exists():
            failures.append(f"Missing file: {rel_path}")
            continue

        if "sha256" not in meta:
            warnings.append(f"No SHA-256 in manifest for: {rel_path}")
            continue
        expected_sha = meta["sha256"]

        actual_sha = sha256_file(file_path)
        if actual_sha != expected_sha:
            failures.append(
                f"Hash mismatch for {rel_path}: "
                f"expected {expected_sha[:12]}... got {actual_sha[:12]}..."
            )
        else:
            checked += 1
            if verbose:
                print(f"  OK  {rel_path} ({actual_sha[:12]}...)")

    if not failures:
        print(f"Verified {checked} files - all OK")
    else:
        print(f"Verified {checked} files - {len(failures)} FAILED")

    return failures, warnings


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Verify Pi backup integrity",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("backup_dir", type=Path, help="Path to backup snapshot directory")
    parser.add_argument("--verbose", action="store_true", help="Show details for all files")
    args = parser.parse_args()

    if not args.backup_dir.is_dir():
        print(f"ERROR: Not a directory: {args.backup_dir}", file=sys.stderr)
        return 1

    failures, warnings = verify_backup(args.backup_dir, verbose=args.verbose)

    for w in warnings:
        print(f"  WARNING: {w}")
    for f in failures:
        print(f"  FAILED: {f}")

    if failures:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
