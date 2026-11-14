"""One lock for every process that commands a relay or the pump."""

import contextlib
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Optional

from src.common.logger import setup_logger

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

logger = setup_logger(__name__)

LOCK_FILE = Path(__file__).resolve().parents[2] / "data" / "control.lock"
POLL_S = 0.2
SLOW_WAIT_S = 1.0


class ControlLockTimeout(Exception):
    """Another process held the control lock for longer than the timeout."""


def _try_lock(fd: int) -> bool:
    try:
        if sys.platform == "win32":
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


@contextlib.contextmanager
def control_lock(timeout_s: float, path: Optional[Path] = None) -> Iterator[None]:
    """Hold the lock for the block. Raises ControlLockTimeout after timeout_s;
    0 tries once. Closing the file releases the lock, also when the process dies."""
    path = LOCK_FILE if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        started = time.monotonic()
        while not _try_lock(fd):
            if time.monotonic() - started >= timeout_s:
                raise ControlLockTimeout(f"{path} held for over {timeout_s} s")
            time.sleep(POLL_S)
        waited = time.monotonic() - started
        if waited > SLOW_WAIT_S:
            logger.warning(f"Waited {waited:.1f} s for the control lock")
        yield
        if sys.platform == "win32":
            # Windows frees a lock left to close() only at some later time
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)
