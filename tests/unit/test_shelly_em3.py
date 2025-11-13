"""Unit tests for Shelly EM3 data collection."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.data_collection.shelly_em3 import (
    fetch_shelly_em3_status,
    process_shelly_em3_data,
)


class TestShellyEM3DataCollection(unittest.TestCase):
    """Test cases for Shelly EM3 data collection functions."""

    def setUp(self):
        """Set up test fixtures."""
        # Sample Shelly EM3 status response
        self.sample_status = {
            "wifi_sta": {
                "connected": True,
                "ssid": "TestNetwork",
                "ip": "192.168.1.5",
                "rssi": -45,
            },
            "cloud": {"enabled": True, "connected": True},
            "mqtt": {"connected": False},
            "time": "16:20",
            "unixtime": 1731503820,
            "serial": 1234,
            "has_update": False,
            "mac": "AABBCCDDEEFF",
            "relays": [{"ison": False}],
            "emeters": [
                {
                    "power": 125.5,
                    "current": 0.55,
                    "voltage": 228.3,
                    "pf": 0.95,
                    "total": 15234.5,
                    "total_returned": 1234.2,
                },
                {
                    "power": 230.8,
                    "current": 1.01,
                    "voltage": 229.1,
                    "pf": 0.98,
                    "total": 18456.3,
                    "total_returned": 2345.6,
                },
                {
                    "power": 189.3,
                    "current": 0.83,
                    "voltage": 227.9,
                    "pf": 0.96,
                    "total": 16789.1,
                    "total_returned": 1789.4,
                },
            ],
            "fs_mounted": True,
            "update": {"status": "idle", "has_update": False},
        }

    def test_process_shelly_em3_data_structure(self):
        """Test that process_shelly_em3_data returns correct field structure."""
        fields = process_shelly_em3_data(self.sample_status)

        # Check all expected per-phase fields
        for phase in [1, 2, 3]:
            self.assertIn(f"phase{phase}_power", fields)
            self.assertIn(f"phase{phase}_current", fields)
            self.assertIn(f"phase{phase}_voltage", fields)
            self.assertIn(f"phase{phase}_pf", fields)
            self.assertIn(f"phase{phase}_total", fields)
            self.assertIn(f"phase{phase}_total_returned", fields)
            self.assertIn(f"phase{phase}_net_total", fields)

        # Check aggregate fields
        self.assertIn("total_power", fields)
        self.assertIn("total_energy", fields)
        self.assertIn("total_energy_returned", fields)
        self.assertIn("net_total_energy", fields)

        # Total of 25 fields (7 per phase * 3 phases + 4 aggregate)
        self.assertEqual(len(fields), 25)

    def test_process_shelly_em3_data_values(self):
        """Test that process_shelly_em3_data calculates values correctly."""
        fields = process_shelly_em3_data(self.sample_status)

        # Check phase 1 values
        self.assertEqual(fields["phase1_power"], 125.5)
        self.assertEqual(fields["phase1_current"], 0.55)
        self.assertEqual(fields["phase1_voltage"], 228.3)
        self.assertEqual(fields["phase1_pf"], 0.95)
        self.assertEqual(fields["phase1_total"], 15234.5)
        self.assertEqual(fields["phase1_total_returned"], 1234.2)
        self.assertAlmostEqual(fields["phase1_net_total"], 14000.3, places=1)

        # Check phase 2 values
        self.assertEqual(fields["phase2_power"], 230.8)
        self.assertEqual(fields["phase2_current"], 1.01)
        self.assertEqual(fields["phase2_voltage"], 229.1)

        # Check phase 3 values
        self.assertEqual(fields["phase3_power"], 189.3)
        self.assertEqual(fields["phase3_current"], 0.83)

        # Check aggregate values
        self.assertAlmostEqual(fields["total_power"], 545.6, places=1)
        self.assertAlmostEqual(fields["total_energy"], 50479.9, places=1)
        self.assertAlmostEqual(fields["total_energy_returned"], 5369.2, places=1)
        self.assertAlmostEqual(fields["net_total_energy"], 45110.7, places=1)

    def test_process_shelly_em3_data_missing_emeters(self):
        """Test that process_shelly_em3_data raises error with invalid data."""
        # Test with missing emeters
        invalid_status = {"wifi_sta": {"connected": True}}
        with self.assertRaises(ValueError) as context:
            process_shelly_em3_data(invalid_status)
        self.assertIn("expected 3 emeters", str(context.exception))

        # Test with wrong number of emeters
        invalid_status = {"emeters": [{"power": 100}, {"power": 200}]}
        with self.assertRaises(ValueError) as context:
            process_shelly_em3_data(invalid_status)
        self.assertIn("expected 3 emeters, got 2", str(context.exception))

    def test_process_shelly_em3_data_missing_fields(self):
        """Test that process_shelly_em3_data handles missing fields gracefully."""
        # Create status with missing power field
        status_missing_fields = {
            "emeters": [
                {
                    "current": 0.5,
                    "voltage": 230.0,
                    "pf": 0.95,
                    "total": 1000.0,
                    "total_returned": 100.0,
                },
                {
                    "current": 0.6,
                    "voltage": 231.0,
                    "pf": 0.96,
                    "total": 2000.0,
                    "total_returned": 200.0,
                },
                {
                    "current": 0.7,
                    "voltage": 229.0,
                    "pf": 0.94,
                    "total": 3000.0,
                    "total_returned": 300.0,
                },
            ]
        }

        fields = process_shelly_em3_data(status_missing_fields)

        # Should default to 0.0 for missing power
        self.assertEqual(fields["phase1_power"], 0.0)
        self.assertEqual(fields["phase2_power"], 0.0)
        self.assertEqual(fields["phase3_power"], 0.0)
        self.assertEqual(fields["total_power"], 0.0)

        # Other fields should still be present
        self.assertEqual(fields["phase1_current"], 0.5)
        self.assertEqual(fields["phase1_voltage"], 230.0)

    def test_process_shelly_em3_data_zero_values(self):
        """Test that process_shelly_em3_data handles zero values correctly."""
        status_zeros = {
            "emeters": [
                {
                    "power": 0.0,
                    "current": 0.0,
                    "voltage": 230.0,
                    "pf": 0.0,
                    "total": 0.0,
                    "total_returned": 0.0,
                },
                {
                    "power": 0.0,
                    "current": 0.0,
                    "voltage": 230.0,
                    "pf": 0.0,
                    "total": 0.0,
                    "total_returned": 0.0,
                },
                {
                    "power": 0.0,
                    "current": 0.0,
                    "voltage": 230.0,
                    "pf": 0.0,
                    "total": 0.0,
                    "total_returned": 0.0,
                },
            ]
        }

        fields = process_shelly_em3_data(status_zeros)

        # All power and energy values should be 0.0
        self.assertEqual(fields["total_power"], 0.0)
        self.assertEqual(fields["total_energy"], 0.0)
        self.assertEqual(fields["total_energy_returned"], 0.0)
        self.assertEqual(fields["net_total_energy"], 0.0)

    def test_process_shelly_em3_data_net_calculation(self):
        """Test that net energy is calculated correctly (consumed - returned)."""
        status = {
            "emeters": [
                {
                    "power": 100.0,
                    "current": 0.5,
                    "voltage": 230.0,
                    "pf": 0.95,
                    "total": 1000.0,
                    "total_returned": 200.0,
                },
                {
                    "power": 100.0,
                    "current": 0.5,
                    "voltage": 230.0,
                    "pf": 0.95,
                    "total": 2000.0,
                    "total_returned": 500.0,
                },
                {
                    "power": 100.0,
                    "current": 0.5,
                    "voltage": 230.0,
                    "pf": 0.95,
                    "total": 3000.0,
                    "total_returned": 1000.0,
                },
            ]
        }

        fields = process_shelly_em3_data(status)

        # Net = consumed - returned
        self.assertEqual(fields["phase1_net_total"], 800.0)  # 1000 - 200
        self.assertEqual(fields["phase2_net_total"], 1500.0)  # 2000 - 500
        self.assertEqual(fields["phase3_net_total"], 2000.0)  # 3000 - 1000
        self.assertEqual(fields["net_total_energy"], 4300.0)  # 6000 - 1700

    @patch("aiohttp.ClientSession")
    async def test_fetch_shelly_em3_status_success(self, mock_session_class):
        """Test successful fetch of Shelly EM3 status."""
        # Mock the response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=self.sample_status)

        # Mock the session context managers
        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value.__aenter__.return_value = mock_session

        # Call the function
        result = await fetch_shelly_em3_status("http://192.168.1.5")

        # Verify the result
        self.assertIsNotNone(result)
        self.assertEqual(result, self.sample_status)
        mock_session.get.assert_called_once()

    @patch("aiohttp.ClientSession")
    async def test_fetch_shelly_em3_status_http_error(self, mock_session_class):
        """Test fetch with HTTP error response."""
        # Mock error response
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value.__aenter__.return_value = mock_session

        # Call the function
        result = await fetch_shelly_em3_status("http://192.168.1.5")

        # Should return None on error
        self.assertIsNone(result)

    @patch("aiohttp.ClientSession")
    async def test_fetch_shelly_em3_status_timeout(self, mock_session_class):
        """Test fetch with timeout."""
        # Mock timeout exception
        mock_session = MagicMock()
        mock_session.get.side_effect = TimeoutError("Timeout")
        mock_session_class.return_value.__aenter__.return_value = mock_session

        # Call the function
        result = await fetch_shelly_em3_status("http://192.168.1.5")

        # Should return None on timeout
        self.assertIsNone(result)

    @patch("aiohttp.ClientSession")
    async def test_fetch_shelly_em3_status_exception(self, mock_session_class):
        """Test fetch with general exception."""
        # Mock general exception
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("Connection error")
        mock_session_class.return_value.__aenter__.return_value = mock_session

        # Call the function
        result = await fetch_shelly_em3_status("http://192.168.1.5")

        # Should return None on exception
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
