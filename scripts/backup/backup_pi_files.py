"""Back up critical Pi configuration files to a local staging directory.

Copies .env, config.yaml, pump_state.json, and systemd unit files,
recording SHA-256 hashes for each file in the returned manifest.
"""

import glob
import hashlib
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Files to back up (relative to project root /opt/redhouse)
# Critical files whose absence is a hard failure
CRITICAL_FILES = [
    ".env",
]

# Optional files whose absence is a warning (may not exist on all deployments)
OPTIONAL_FILES = [
    "config/config.yaml",
    "data/pump_state.json",
]

SYSTEMD_PATTERN = "/etc/systemd/system/redhouse-*"


@dataclass
class BackupResult:
    """Result of a backup operation."""

    success: bool
    files: dict[str, dict[str, str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_pi_files(
    project_root: Path,
    backup_dir: Path,
) -> BackupResult:
    """Copy critical Pi files to backup_dir and record SHA-256 hashes.

    Args:
        project_root: Path to the redhouse project (e.g. /opt/redhouse)
        backup_dir: Destination directory for this backup snapshot

    Returns:
        BackupResult with file manifest and any errors
    """
    result = BackupResult(success=True)

    # Back up project files
    for rel_path in CRITICAL_FILES + OPTIONAL_FILES:
        is_critical = rel_path in CRITICAL_FILES
        src = project_root / rel_path
        if not src.exists():
            msg = f"Source file not found: {src}"
            if is_critical:
                logger.error(msg)
                result.errors.append(msg)
                result.success = False
            else:
                logger.warning(msg)
                result.errors.append(msg)
            continue

        dst = backup_dir / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(str(src), str(dst))
            sha = sha256_file(dst)
            result.files[rel_path] = {
                "sha256": sha,
                "size_bytes": str(dst.stat().st_size),
            }
            logger.info("Backed up %s (sha256: %s)", rel_path, sha[:12])
        except OSError as e:
            msg = f"Failed to copy {rel_path}: {e}"
            logger.error(msg)
            result.errors.append(msg)
            result.success = False

    # Back up systemd unit files
    systemd_dir = backup_dir / "systemd"
    systemd_dir.mkdir(parents=True, exist_ok=True)

    unit_files = sorted(glob.glob(SYSTEMD_PATTERN))
    if not unit_files:
        logger.info("No systemd unit files found matching %s", SYSTEMD_PATTERN)
    for unit_path_str in unit_files:
        unit_path = Path(unit_path_str)
        dst = systemd_dir / unit_path.name
        try:
            shutil.copy2(str(unit_path), str(dst))
            sha = sha256_file(dst)
            key = f"systemd/{unit_path.name}"
            result.files[key] = {
                "sha256": sha,
                "size_bytes": str(dst.stat().st_size),
            }
            logger.debug("Backed up %s", key)
        except OSError as e:
            msg = f"Failed to copy systemd unit {unit_path.name}: {e}"
            logger.error(msg)
            result.errors.append(msg)
            result.success = False

    if unit_files:
        logger.info("Backed up %d systemd unit files", len(unit_files))

    return result
