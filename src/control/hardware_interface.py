"""
Hardware interface abstraction for pump control.

This module defines the abstract interface for heat pump hardware control,
enabling dependency injection and improved testability.
"""

from abc import ABC, abstractmethod
from typing import Optional


class PumpHardwareInterface(ABC):
    """Abstract interface for heat pump hardware control."""

    @abstractmethod
    def write_pump_command(self, command: str) -> bool:
        """
        Write command to heat pump (ON/ALE/EVU).

        Args:
            command: Command to execute (ON/ALE/EVU)

        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    def control_circulation_pump(self, turn_on: bool) -> bool:
        """
        Control AC circulation pump via relay.

        Args:
            turn_on: True to turn on, False to turn off

        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    def get_pump_status(self) -> Optional[dict]:
        """
        Get current pump status from hardware.

        Returns:
            Status dict or None if failed
        """
        pass
