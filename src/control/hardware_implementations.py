"""
Concrete implementations of hardware interfaces for pump control.

This module provides implementations for:
- I2C hardware (geothermal pump control)
- Shelly relay (AC circulation pump control)
- Combined interface (I2C + Shelly)
- Mock interface (testing and dry-run)
"""

import logging
import time
from typing import Callable, Optional

import requests

from src.control.hardware_interface import PumpHardwareInterface

logger = logging.getLogger(__name__)


class I2CHardwareInterface(PumpHardwareInterface):
    """Real I2C hardware implementation for MLP pump control."""

    # I2C register addresses
    I2C_REG1 = 0x01
    I2C_REG2 = 0x02

    # I2C command values
    I2C_COMMANDS = {
        "ON": (0x00, 0x00),  # Normal heating mode
        "ALE": (0xFF, 0x00),  # Lower temperature mode
        "EVU": (0xFF, 0xFF),  # EVU-OFF (pump disabled)
    }

    def __init__(self, i2c_bus: int, i2c_address: int):
        """
        Initialize I2C interface.

        Args:
            i2c_bus: I2C bus number
            i2c_address: I2C device address
        """
        self.bus = i2c_bus
        self.address = i2c_address

        # Import and check availability
        try:
            from smbus2 import SMBus

            self.SMBus = SMBus
            self.available = True
        except ImportError:
            self.available = False
            logger.warning("smbus2 not available - I2C control disabled")

    def write_pump_command(self, command: str) -> bool:
        """Write command to pump via I2C."""
        if not self.available:
            logger.error("I2C not available")
            return False

        if command not in self.I2C_COMMANDS:
            logger.error(f"Unknown command: {command}")
            return False

        reg1, reg2 = self.I2C_COMMANDS[command]

        try:
            with self.SMBus(self.bus) as bus:
                bus.write_byte_data(self.address, self.I2C_REG1, reg1)
                bus.write_byte_data(self.address, self.I2C_REG2, reg2)
            logger.debug(f"I2C write successful: {command} (0x{reg1:02X}, 0x{reg2:02X})")
            return True
        except Exception as e:
            logger.error(f"I2C write failed: {e}")
            return False

    def control_circulation_pump(self, turn_on: bool) -> bool:
        """I2C interface doesn't control circulation pump directly."""
        # This would be handled by Shelly relay
        return True

    def get_pump_status(self) -> Optional[dict]:
        """I2C interface doesn't provide status reading."""
        return None


class ShellyRelayInterface(PumpHardwareInterface):
    """Shelly relay implementation for AC pump control."""

    def __init__(self, relay_url: str):
        """
        Initialize Shelly relay interface.

        Args:
            relay_url: Shelly relay URL (e.g., http://192.168.1.5/relay/0)
        """
        self.relay_url = relay_url

    def write_pump_command(self, command: str) -> bool:
        """Shelly relay doesn't control pump commands."""
        # This would be handled by I2C interface
        return True

    def control_circulation_pump(self, turn_on: bool) -> bool:
        """Control AC pump via Shelly relay HTTP API."""
        action = "on" if turn_on else "off"
        url = f"{self.relay_url}?turn={action}"

        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                logger.info(f"AC pump turned {action}")
                return True
            else:
                logger.error(f"Failed to control AC pump: HTTP {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Failed to control AC pump: {e}")
            return False

    def get_pump_status(self) -> Optional[dict]:
        """Get Shelly relay status."""
        try:
            response = requests.get(self.relay_url, timeout=5)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception:
            return None


class CombinedHardwareInterface(PumpHardwareInterface):
    """Combined I2C + Shelly relay implementation."""

    def __init__(self, i2c_bus: int, i2c_address: int, relay_url: str):
        """
        Initialize combined hardware interface.

        Args:
            i2c_bus: I2C bus number
            i2c_address: I2C device address
            relay_url: Shelly relay URL
        """
        self.i2c = I2CHardwareInterface(i2c_bus, i2c_address)
        self.shelly = ShellyRelayInterface(relay_url)

    def write_pump_command(self, command: str) -> bool:
        """Write command via I2C."""
        return self.i2c.write_pump_command(command)

    def control_circulation_pump(self, turn_on: bool) -> bool:
        """Control AC pump via Shelly relay."""
        return self.shelly.control_circulation_pump(turn_on)

    def get_pump_status(self) -> Optional[dict]:
        """Get status from Shelly relay."""
        return self.shelly.get_pump_status()


class MockHardwareInterface(PumpHardwareInterface):
    """Mock implementation for testing and dry-run mode."""

    def __init__(self):
        """Initialize mock hardware."""
        self.commands_executed = []
        self.pump_on = False
        self.circulation_pump_on = False
        self.command_success = True  # Can be set to False to simulate failures

    def write_pump_command(self, command: str) -> bool:
        """Record command execution."""
        self.commands_executed.append(command)
        logger.info(f"MOCK: Would execute pump command: {command}")
        return self.command_success

    def control_circulation_pump(self, turn_on: bool) -> bool:
        """Record circulation pump control."""
        self.circulation_pump_on = turn_on
        logger.info(f"MOCK: Would control AC pump: {'on' if turn_on else 'off'}")
        return self.command_success

    def get_pump_status(self) -> Optional[dict]:
        """Return simulated status."""
        return {
            "mode": "MOCK",
            "status": "simulated",
            "ison": self.circulation_pump_on,
            "commands_executed": len(self.commands_executed),
        }


class GarageRelay:
    """One garage heater on a Shelly 1 (Gen1) relay. An OFF always carries a
    flip-back timer, so a stopped controller leaves the heater ON."""

    TRIES = 3
    RETRY_WAIT_S = 2.0
    TIMEOUT_S = 5.0

    def __init__(self, url: str, sleep: Callable[[float], None] = time.sleep):
        self.url = url
        self._sleep = sleep

    def _get(self, query: str) -> Optional[dict]:
        """The relay's status reply, or None after every try failed."""
        for attempt in range(1, self.TRIES + 1):
            try:
                response = requests.get(self.url + query, timeout=self.TIMEOUT_S)
                response.raise_for_status()
                reply = response.json()
                if not isinstance(reply, dict) or "ison" not in reply:
                    raise ValueError(f"no ison in {reply!r}")
                return reply
            except (requests.RequestException, ValueError, TypeError) as e:
                logger.warning(f"Garage relay {self.url} try {attempt}: {e}")
                if attempt < self.TRIES:
                    self._sleep(self.RETRY_WAIT_S)
        return None

    def set(self, on: bool, off_timer_s: int) -> Optional[bool]:
        """The relay state after the command, or None. An OFF without a running
        flip-back timer is a failure: the heater would not come back ON."""
        reply = self._get("?turn=on" if on else f"?turn=off&timer={off_timer_s}")
        if reply is None:
            return None
        if not on and not ("has_timer" in reply and reply["has_timer"]):
            logger.error(f"Garage relay {self.url} is OFF without its flip-back timer")
            return None
        return bool(reply["ison"])

    def state(self) -> Optional[bool]:
        reply = self._get("")
        return None if reply is None else bool(reply["ison"])


class MockGarageRelay:
    """Garage relay for staging, dry runs and tests."""

    def __init__(self, url: str, ison: bool = False):
        self.url = url
        self.ison = ison
        self.commands: list = []

    def set(self, on: bool, off_timer_s: int) -> Optional[bool]:
        self.commands.append((on, off_timer_s))
        self.ison = on
        logger.info(f"MOCK: garage relay {self.url} {'ON' if on else 'OFF'}")
        return self.ison

    def state(self) -> Optional[bool]:
        return self.ison
