"""Tests for the append-only data log mirror in the nightly Pi backup."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).parents[2]
sys.path.insert(0, str(REPO))

from scripts.backup.run_backup_pi import _rsync_data_log_archive  # noqa: E402

NAS = ("nas.local", "backup-user", "/home/pi/.ssh/key")
ARCHIVE = "/share/Backups/redhouse/data_archive"


class Completed:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


class TestRsyncDataLogArchive:
    def test_missing_directory_is_not_a_failure(self, tmp_path):
        """A collector that has never run leaves no data_logs."""
        with patch("scripts.backup.run_backup_pi.subprocess.run") as run:
            ok, err = _rsync_data_log_archive(tmp_path / "absent", *NAS, ARCHIVE)
        assert ok is True
        assert err == ""
        run.assert_not_called()

    def test_command_is_append_only(self, tmp_path):
        """No --delete: the NAS must keep what the Pi's retention drops."""
        (tmp_path / "weather").mkdir()
        with patch("scripts.backup.run_backup_pi.subprocess.run", return_value=Completed()) as run:
            ok, err = _rsync_data_log_archive(tmp_path, *NAS, ARCHIVE)
        assert ok is True and err == ""
        cmd = run.call_args[0][0]
        assert "--delete" not in cmd
        assert "--ignore-existing" in cmd
        # rsync 3.2.3 on the Pi supports this, so the NAS directory does
        # not have to be created by hand first.
        assert "--mkpath" in cmd
        assert cmd[-1] == f"{NAS[1]}@{NAS[0]}:{ARCHIVE}/"

    def test_destination_is_not_the_dated_snapshot(self, tmp_path):
        """Snapshots rotate 30 deep; gigabytes of logs must not be in them."""
        (tmp_path / "windpower").mkdir()
        with patch("scripts.backup.run_backup_pi.subprocess.run", return_value=Completed()) as run:
            _rsync_data_log_archive(tmp_path, *NAS, ARCHIVE)
        dest = run.call_args[0][0][-1]
        assert ARCHIVE in dest
        assert "2026-" not in dest

    def test_rsync_failure_is_reported(self, tmp_path):
        (tmp_path / "weather").mkdir()
        with patch(
            "scripts.backup.run_backup_pi.subprocess.run",
            return_value=Completed(returncode=23, stderr="permission denied"),
        ):
            ok, err = _rsync_data_log_archive(tmp_path, *NAS, ARCHIVE)
        assert ok is False
        assert "23" in err and "permission denied" in err

    def test_timeout_is_reported(self, tmp_path):
        (tmp_path / "weather").mkdir()
        with patch(
            "scripts.backup.run_backup_pi.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="rsync", timeout=1800),
        ):
            ok, err = _rsync_data_log_archive(tmp_path, *NAS, ARCHIVE)
        assert ok is False
        assert "timed out" in err

    def test_missing_rsync_is_reported(self, tmp_path):
        (tmp_path / "weather").mkdir()
        with patch(
            "scripts.backup.run_backup_pi.subprocess.run",
            side_effect=FileNotFoundError("rsync"),
        ):
            ok, err = _rsync_data_log_archive(tmp_path, *NAS, ARCHIVE)
        assert ok is False
        assert "not found" in err

    def test_timeout_is_generous_enough_for_a_first_sync(self, tmp_path):
        """The first run uploads months of logs, not one night's worth."""
        (tmp_path / "weather").mkdir()
        with patch("scripts.backup.run_backup_pi.subprocess.run", return_value=Completed()) as run:
            _rsync_data_log_archive(tmp_path, *NAS, ARCHIVE)
        assert run.call_args.kwargs["timeout"] >= 900
