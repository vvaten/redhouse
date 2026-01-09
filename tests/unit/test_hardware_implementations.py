"""
Unit tests for hardware interface implementations.

Tests concrete implementations of PumpHardwareInterface without requiring
actual hardware. Uses mocking for external dependencies (smbus2, requests).
"""

from unittest.mock import MagicMock, Mock, patch

from src.control.hardware_implementations import (
    CombinedHardwareInterface,
    I2CHardwareInterface,
    MockHardwareInterface,
    ShellyRelayInterface,
)


class TestI2CHardwareInterface:
    """Test I2C hardware interface."""

    def test_init_with_smbus2_available(self):
        """Test initialization when smbus2 is available."""
        mock_smbus = MagicMock()
        with patch.dict("sys.modules", {"smbus2": MagicMock(SMBus=mock_smbus)}):
            interface = I2CHardwareInterface()
            assert interface.available is True
            assert interface.bus == 1
            assert interface.address == 0x10

    def test_init_with_custom_config(self):
        """Test initialization with custom I2C configuration."""
        mock_smbus = MagicMock()
        with patch.dict("sys.modules", {"smbus2": MagicMock(SMBus=mock_smbus)}):
            interface = I2CHardwareInterface(i2c_bus=2, i2c_address=0x20)
            assert interface.bus == 2
            assert interface.address == 0x20

    def test_init_without_smbus2(self):
        """Test initialization when smbus2 is not available."""
        with patch.dict("sys.modules", {"smbus2": None}):
            interface = I2CHardwareInterface()
            assert interface.available is False

    def test_write_pump_command_success(self):
        """Test successful I2C command write."""
        mock_smbus_class = MagicMock()
        mock_smbus_instance = MagicMock()
        mock_smbus_class.return_value = mock_smbus_instance

        with patch.dict("sys.modules", {"smbus2": MagicMock(SMBus=mock_smbus_class)}):
            interface = I2CHardwareInterface()
            result = interface.write_pump_command("ON")

            assert result is True
            assert mock_smbus_instance.__enter__().write_byte_data.call_count == 2

    def test_write_pump_command_all_commands(self):
        """Test all valid pump commands."""
        mock_smbus_class = MagicMock()
        mock_smbus_instance = MagicMock()
        mock_smbus_class.return_value = mock_smbus_instance

        with patch.dict("sys.modules", {"smbus2": MagicMock(SMBus=mock_smbus_class)}):
            interface = I2CHardwareInterface()

            # Test ON command
            result = interface.write_pump_command("ON")
            assert result is True

            # Test ALE command
            result = interface.write_pump_command("ALE")
            assert result is True

            # Test EVU command
            result = interface.write_pump_command("EVU")
            assert result is True

    def test_write_pump_command_invalid_command(self):
        """Test invalid command handling."""
        mock_smbus_class = MagicMock()
        with patch.dict("sys.modules", {"smbus2": MagicMock(SMBus=mock_smbus_class)}):
            interface = I2CHardwareInterface()
            result = interface.write_pump_command("INVALID")

            assert result is False

    def test_write_pump_command_i2c_unavailable(self):
        """Test command write when I2C is unavailable."""
        with patch.dict("sys.modules", {"smbus2": None}):
            interface = I2CHardwareInterface()
            result = interface.write_pump_command("ON")

            assert result is False

    def test_write_pump_command_i2c_exception(self):
        """Test command write when I2C raises exception."""
        mock_smbus_class = MagicMock()
        mock_smbus_instance = MagicMock()
        mock_smbus_instance.__enter__().write_byte_data.side_effect = Exception("I2C error")
        mock_smbus_class.return_value = mock_smbus_instance

        with patch.dict("sys.modules", {"smbus2": MagicMock(SMBus=mock_smbus_class)}):
            interface = I2CHardwareInterface()
            result = interface.write_pump_command("ON")

            assert result is False

    def test_control_circulation_pump(self):
        """Test circulation pump control (not implemented for I2C)."""
        mock_smbus_class = MagicMock()
        with patch.dict("sys.modules", {"smbus2": MagicMock(SMBus=mock_smbus_class)}):
            interface = I2CHardwareInterface()
            result = interface.control_circulation_pump(True)

            assert result is True  # Always returns True (handled by Shelly)

    def test_get_pump_status(self):
        """Test pump status retrieval (not implemented for I2C)."""
        mock_smbus_class = MagicMock()
        with patch.dict("sys.modules", {"smbus2": MagicMock(SMBus=mock_smbus_class)}):
            interface = I2CHardwareInterface()
            status = interface.get_pump_status()

            assert status is None


class TestShellyRelayInterface:
    """Test Shelly relay interface."""

    def test_init_default_url(self):
        """Test initialization with default URL."""
        interface = ShellyRelayInterface()
        assert interface.relay_url == "http://192.168.1.5/relay/0"

    def test_init_custom_url(self):
        """Test initialization with custom URL."""
        custom_url = "http://192.168.1.100/relay/1"
        interface = ShellyRelayInterface(relay_url=custom_url)
        assert interface.relay_url == custom_url

    @patch("src.control.hardware_implementations.requests.get")
    def test_control_circulation_pump_on_success(self, mock_get):
        """Test turning circulation pump on."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        interface = ShellyRelayInterface()
        result = interface.control_circulation_pump(True)

        assert result is True
        mock_get.assert_called_once_with("http://192.168.1.5/relay/0?turn=on", timeout=5)

    @patch("src.control.hardware_implementations.requests.get")
    def test_control_circulation_pump_off_success(self, mock_get):
        """Test turning circulation pump off."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        interface = ShellyRelayInterface()
        result = interface.control_circulation_pump(False)

        assert result is True
        mock_get.assert_called_once_with("http://192.168.1.5/relay/0?turn=off", timeout=5)

    @patch("src.control.hardware_implementations.requests.get")
    def test_control_circulation_pump_http_error(self, mock_get):
        """Test circulation pump control with HTTP error."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        interface = ShellyRelayInterface()
        result = interface.control_circulation_pump(True)

        assert result is False

    @patch("src.control.hardware_implementations.requests.get")
    def test_control_circulation_pump_exception(self, mock_get):
        """Test circulation pump control with exception."""
        mock_get.side_effect = Exception("Network error")

        interface = ShellyRelayInterface()
        result = interface.control_circulation_pump(True)

        assert result is False

    @patch("src.control.hardware_implementations.requests.get")
    def test_get_pump_status_success(self, mock_get):
        """Test getting pump status successfully."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ison": True, "mode": "relay"}
        mock_get.return_value = mock_response

        interface = ShellyRelayInterface()
        status = interface.get_pump_status()

        assert status == {"ison": True, "mode": "relay"}

    @patch("src.control.hardware_implementations.requests.get")
    def test_get_pump_status_http_error(self, mock_get):
        """Test getting pump status with HTTP error."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        interface = ShellyRelayInterface()
        status = interface.get_pump_status()

        assert status is None

    @patch("src.control.hardware_implementations.requests.get")
    def test_get_pump_status_exception(self, mock_get):
        """Test getting pump status with exception."""
        mock_get.side_effect = Exception("Network error")

        interface = ShellyRelayInterface()
        status = interface.get_pump_status()

        assert status is None

    def test_write_pump_command(self):
        """Test pump command write (not implemented for Shelly)."""
        interface = ShellyRelayInterface()
        result = interface.write_pump_command("ON")

        assert result is True  # Always returns True (handled by I2C)


class TestCombinedHardwareInterface:
    """Test combined hardware interface."""

    def test_init_default_config(self):
        """Test initialization with default configuration."""
        mock_smbus_class = MagicMock()
        with patch.dict("sys.modules", {"smbus2": MagicMock(SMBus=mock_smbus_class)}):
            interface = CombinedHardwareInterface()
            assert interface.i2c is not None
            assert interface.shelly is not None

    def test_init_custom_config(self):
        """Test initialization with custom configuration."""
        mock_smbus_class = MagicMock()
        with patch.dict("sys.modules", {"smbus2": MagicMock(SMBus=mock_smbus_class)}):
            interface = CombinedHardwareInterface(
                i2c_bus=2, i2c_address=0x20, relay_url="http://192.168.1.100/relay/1"
            )
            assert interface.i2c.bus == 2
            assert interface.i2c.address == 0x20
            assert interface.shelly.relay_url == "http://192.168.1.100/relay/1"

    def test_write_pump_command_delegates_to_i2c(self):
        """Test that pump command is delegated to I2C."""
        mock_smbus_class = MagicMock()
        with patch.dict("sys.modules", {"smbus2": MagicMock(SMBus=mock_smbus_class)}):
            interface = CombinedHardwareInterface()
            interface.i2c.write_pump_command = Mock(return_value=True)

            result = interface.write_pump_command("ON")

            assert result is True
            interface.i2c.write_pump_command.assert_called_once_with("ON")

    @patch("src.control.hardware_implementations.requests.get")
    def test_control_circulation_pump_delegates_to_shelly(self, mock_get):
        """Test that circulation pump control is delegated to Shelly."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        mock_smbus_class = MagicMock()
        with patch.dict("sys.modules", {"smbus2": MagicMock(SMBus=mock_smbus_class)}):
            interface = CombinedHardwareInterface()
            result = interface.control_circulation_pump(True)

            assert result is True

    @patch("src.control.hardware_implementations.requests.get")
    def test_get_pump_status_delegates_to_shelly(self, mock_get):
        """Test that pump status is delegated to Shelly."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ison": True}
        mock_get.return_value = mock_response

        mock_smbus_class = MagicMock()
        with patch.dict("sys.modules", {"smbus2": MagicMock(SMBus=mock_smbus_class)}):
            interface = CombinedHardwareInterface()
            status = interface.get_pump_status()

            assert status == {"ison": True}


class TestMockHardwareInterface:
    """Test mock hardware interface."""

    def test_init(self):
        """Test initialization of mock hardware."""
        interface = MockHardwareInterface()
        assert interface.commands_executed == []
        assert interface.pump_on is False
        assert interface.circulation_pump_on is False
        assert interface.command_success is True

    def test_write_pump_command_success(self):
        """Test successful pump command execution."""
        interface = MockHardwareInterface()
        result = interface.write_pump_command("ON")

        assert result is True
        assert interface.commands_executed == ["ON"]

    def test_write_pump_command_multiple_commands(self):
        """Test multiple pump commands are recorded."""
        interface = MockHardwareInterface()
        interface.write_pump_command("ON")
        interface.write_pump_command("ALE")
        interface.write_pump_command("EVU")

        assert interface.commands_executed == ["ON", "ALE", "EVU"]

    def test_write_pump_command_failure(self):
        """Test pump command failure simulation."""
        interface = MockHardwareInterface()
        interface.command_success = False

        result = interface.write_pump_command("ON")

        assert result is False
        assert interface.commands_executed == ["ON"]  # Still recorded

    def test_control_circulation_pump_on(self):
        """Test turning circulation pump on."""
        interface = MockHardwareInterface()
        result = interface.control_circulation_pump(True)

        assert result is True
        assert interface.circulation_pump_on is True

    def test_control_circulation_pump_off(self):
        """Test turning circulation pump off."""
        interface = MockHardwareInterface()
        interface.circulation_pump_on = True

        result = interface.control_circulation_pump(False)

        assert result is True
        assert interface.circulation_pump_on is False

    def test_control_circulation_pump_failure(self):
        """Test circulation pump control failure simulation."""
        interface = MockHardwareInterface()
        interface.command_success = False

        result = interface.control_circulation_pump(True)

        assert result is False

    def test_get_pump_status(self):
        """Test pump status retrieval."""
        interface = MockHardwareInterface()
        interface.write_pump_command("ON")
        interface.write_pump_command("ALE")
        interface.control_circulation_pump(True)

        status = interface.get_pump_status()

        assert status is not None
        assert status["mode"] == "MOCK"
        assert status["status"] == "simulated"
        assert status["ison"] is True
        assert status["commands_executed"] == 2

    def test_reset_state(self):
        """Test that mock can be reset for new test."""
        interface = MockHardwareInterface()
        interface.write_pump_command("ON")
        interface.control_circulation_pump(True)

        # Reset state
        interface.commands_executed = []
        interface.circulation_pump_on = False

        assert interface.commands_executed == []
        assert interface.circulation_pump_on is False
