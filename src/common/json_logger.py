#!/usr/bin/env python
"""JSON data logging for backup and recovery purposes."""

import datetime
import json
from pathlib import Path
from typing import Any, Optional

from src.common.logger import setup_logger

logger = setup_logger(__name__)


class JSONDataLogger:
    """
    Logs fetched data to timestamped JSON files for backup.

    Keeps up to 7 days of logs. If database insertion fails, data can be
    retroactively recovered from these JSON logs.
    """

    def __init__(self, data_source_name: str, log_dir: str = "data_logs"):
        """
        Initialize JSON data logger.

        Args:
            data_source_name: Name of data source (e.g., 'spot_prices', 'checkwatt')
            log_dir: Directory to store log files
        """
        self.data_source_name = data_source_name
        self.log_dir = Path(log_dir) / data_source_name
        self.retention_days = 7

        # Create log directory if it doesn't exist
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _get_log_filename(self, timestamp: Optional[datetime.datetime] = None) -> Path:
        """
        Get log filename for a given timestamp.

        Args:
            timestamp: Timestamp to use (defaults to now)

        Returns:
            Path to log file
        """
        if timestamp is None:
            timestamp = datetime.datetime.now()

        # Format: YYYYMMDD_HHMMSS.json
        filename = timestamp.strftime("%Y%m%d_%H%M%S.json")
        return self.log_dir / filename

    def log_data(self, data: Any, metadata: Optional[dict] = None) -> bool:
        """
        Log data to timestamped JSON file.

        Args:
            data: Data to log (must be JSON-serializable)
            metadata: Optional metadata to include (e.g., date range, record count)

        Returns:
            True if successful
        """
        try:
            timestamp = datetime.datetime.now()
            log_file = self._get_log_filename(timestamp)

            log_entry = {
                "timestamp": timestamp.isoformat(),
                "data_source": self.data_source_name,
                "metadata": metadata or {},
                "data": data,
            }

            with open(log_file, "w") as f:
                json.dump(log_entry, f, indent=2, default=str)

            logger.info(f"Logged data to {log_file}")
            return True

        except Exception as e:
            logger.error(f"Failed to log data to JSON file: {e}")
            return False

    def cleanup_old_logs(self) -> int:
        """
        Remove log files older than retention_days.

        Returns:
            Number of files deleted
        """
        try:
            cutoff_time = datetime.datetime.now() - datetime.timedelta(days=self.retention_days)
            deleted_count = 0

            for log_file in self.log_dir.glob("*.json"):
                try:
                    # Get file modification time
                    mtime = datetime.datetime.fromtimestamp(log_file.stat().st_mtime)

                    if mtime < cutoff_time:
                        log_file.unlink()
                        deleted_count += 1
                        logger.debug(f"Deleted old log file: {log_file}")

                except Exception as e:
                    logger.warning(f"Error processing log file {log_file}: {e}")
                    continue

            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old log files")

            return deleted_count

        except Exception as e:
            logger.error(f"Failed to cleanup old logs: {e}")
            return 0

    def get_recent_logs(self, days: int = 7) -> list[Path]:
        """
        Get list of recent log files.

        Args:
            days: Number of days to look back

        Returns:
            List of log file paths, sorted by modification time (newest first)
        """
        try:
            cutoff_time = datetime.datetime.now() - datetime.timedelta(days=days)
            recent_logs = []

            for log_file in self.log_dir.glob("*.json"):
                try:
                    mtime = datetime.datetime.fromtimestamp(log_file.stat().st_mtime)
                    if mtime >= cutoff_time:
                        recent_logs.append((mtime, log_file))
                except Exception as e:
                    logger.warning(f"Error checking log file {log_file}: {e}")
                    continue

            # Sort by modification time (newest first)
            recent_logs.sort(reverse=True)
            return [log_file for _, log_file in recent_logs]

        except Exception as e:
            logger.error(f"Failed to get recent logs: {e}")
            return []

    def load_log(self, log_file: Path) -> Optional[dict]:
        """
        Load data from a log file.

        Args:
            log_file: Path to log file

        Returns:
            Log entry dict, or None if failed
        """
        try:
            with open(log_file) as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load log file {log_file}: {e}")
            return None
