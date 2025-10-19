"""Logging utilities for home automation system"""

import logging
import logging.handlers
from pathlib import Path
from typing import Optional

from .config import get_config


def setup_logger(
    name: str, log_file: Optional[str] = None, level: Optional[str] = None
) -> logging.Logger:
    """
    Set up a logger with console and rotating file handlers

    Args:
        name: Logger name (typically __name__)
        log_file: Log file name (will be placed in LOG_DIR from config)
        level: Log level (default from config)

    Returns:
        Configured logger
    """
    config = get_config()

    logger = logging.getLogger(name)

    # Set level
    if level is None:
        level = config.log_level
    logger.setLevel(getattr(logging, level.upper()))

    # Avoid duplicate handlers
    if logger.handlers:
        return logger

    # Console handler
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # File handler (if log_file specified)
    if log_file:
        try:
            log_dir = Path(config.log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)

            log_path = log_dir / log_file

            file_handler = logging.handlers.RotatingFileHandler(
                log_path, maxBytes=config.log_max_bytes, backupCount=config.log_backup_count
            )
            file_formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
            )
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)
        except (PermissionError, OSError) as e:
            # In CI or restricted environments, log directory may not be writable
            # Continue with console-only logging
            logger.warning(f"Could not create log file {log_file}: {e}. Using console logging only.")

    return logger
