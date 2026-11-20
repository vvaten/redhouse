"""A deadline for a whole run: log where it stuck, then end the process.

A thread timer, not SIGALRM, so it also runs on Windows, where the checks run.
"""

import logging
import os
import threading
from typing import Callable, Optional

from src.common.logger import setup_logger

logger = setup_logger(__name__)

# The process ends this long after the deadline even if the log write hangs
BACKSTOP_S = 2.0


class Watchdog:
    def __init__(
        self,
        limit_s: float,
        on_expire: Optional[Callable[[str], None]] = None,
        log: Optional[logging.Logger] = None,
    ):
        self.limit_s = limit_s
        self.stage = "start"
        self._on_expire = on_expire if on_expire is not None else _exit
        self._log = log if log is not None else logger
        self._timer = threading.Timer(limit_s, self._expire)
        self._timer.daemon = True

    def mark(self, stage: str) -> None:
        self.stage = stage

    def start(self) -> "Watchdog":
        self._timer.start()
        return self

    def cancel(self) -> None:
        self._timer.cancel()

    def _expire(self) -> None:
        stage = self.stage
        backstop = threading.Timer(BACKSTOP_S, self._on_expire, (stage,))
        backstop.daemon = True
        backstop.start()
        self._log.error(f"Run over {self.limit_s:.0f} s, stuck in: {stage}")
        self._on_expire(stage)
        backstop.cancel()


def _exit(stage: str) -> None:
    # A stuck run holds no state worth saving; os._exit ends even a blocked thread
    os._exit(1)
