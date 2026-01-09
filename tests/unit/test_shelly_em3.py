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

    @patch("src.data_collection.shelly_em3.get_config")
    @patch("src.data_collection.shelly_em3.InfluxClient")
    async def test_write_shelly_em3_to_influx_success(self, mock_influx_class, mock_get_config):
        """Test successful write to InfluxDB."""
        from src.data_collection.shelly_em3 import write_shelly_em3_to_influx

        # Mock config
        mock_config = MagicMock()
        mock_config.influxdb_bucket_shelly_em3_raw = "shelly_em3_raw"
        mock_get_config.return_value = mock_config

        # Mock InfluxClient
        mock_influx = MagicMock()
        mock_influx.write_point = MagicMock(return_value=True)
        mock_influx_class.return_value = mock_influx

        fields = {"total_power": 545.6, "total_energy": 50479.9}

        result = await write_shelly_em3_to_influx(fields, dry_run=False)

        self.assertTrue(result)
        mock_influx.write_point.assert_called_once()

    async def test_write_shelly_em3_to_influx_dry_run(self):
        """Test write in dry-run mode."""
        from src.data_collection.shelly_em3 import write_shelly_em3_to_influx

        fields = {"total_power": 545.6}

        result = await write_shelly_em3_to_influx(fields, dry_run=True)

        self.assertTrue(result)
        # Should not call InfluxDB in dry-run mode

    @patch("src.data_collection.shelly_em3.get_config")
    @patch("src.data_collection.shelly_em3.InfluxClient")
    async def test_write_shelly_em3_to_influx_exception(self, mock_influx_class, mock_get_config):
        """Test write handles exceptions."""
        from src.data_collection.shelly_em3 import write_shelly_em3_to_influx

        # Mock config
        mock_config = MagicMock()
        mock_config.influxdb_bucket_shelly_em3_raw = "shelly_em3_raw"
        mock_get_config.return_value = mock_config

        # Mock InfluxClient to raise exception
        mock_influx_class.side_effect = Exception("Connection error")

        fields = {"total_power": 545.6}

        result = await write_shelly_em3_to_influx(fields, dry_run=False)

        self.assertFalse(result)

    @patch("src.data_collection.shelly_em3.JSONDataLogger")
    @patch("src.data_collection.shelly_em3.write_shelly_em3_to_influx")
    @patch("src.data_collection.shelly_em3.fetch_shelly_em3_status")
    async def test_collect_shelly_em3_data_success(
        self, mock_fetch, mock_write, mock_json_logger_class
    ):
        """Test successful data collection."""
        from src.data_collection.shelly_em3 import collect_shelly_em3_data

        # Set environment variable
        with patch.dict("os.environ", {"SHELLY_EM3_URL": "http://192.168.1.5"}):
            # Mock fetch to return valid data
            mock_fetch.return_value = self.sample_status

            # Mock write to succeed
            mock_write.return_value = True

            # Mock JSON logger
            mock_json_logger = MagicMock()
            mock_json_logger_class.return_value = mock_json_logger

            result = await collect_shelly_em3_data(dry_run=False)

            self.assertEqual(result, 0)  # Success
            mock_fetch.assert_called_once_with("http://192.168.1.5")
            mock_write.assert_called_once()
            mock_json_logger.log_data.assert_called_once()
            mock_json_logger.cleanup_old_logs.assert_called_once()

    async def test_collect_shelly_em3_data_no_url(self):
        """Test collection fails when SHELLY_EM3_URL not set."""
        from src.data_collection.shelly_em3 import collect_shelly_em3_data

        # Ensure env var is not set
        with patch.dict("os.environ", {}, clear=True):
            result = await collect_shelly_em3_data(dry_run=False)

            self.assertEqual(result, 1)  # Failure

    @patch("src.data_collection.shelly_em3.fetch_shelly_em3_status")
    async def test_collect_shelly_em3_data_fetch_fails(self, mock_fetch):
        """Test collection fails when fetch returns None."""
        from src.data_collection.shelly_em3 import collect_shelly_em3_data

        with patch.dict("os.environ", {"SHELLY_EM3_URL": "http://192.168.1.5"}):
            # Mock fetch to fail
            mock_fetch.return_value = None

            result = await collect_shelly_em3_data(dry_run=False)

            self.assertEqual(result, 1)  # Failure

    @patch("src.data_collection.shelly_em3.JSONDataLogger")
    @patch("src.data_collection.shelly_em3.fetch_shelly_em3_status")
    async def test_collect_shelly_em3_data_process_fails(self, mock_fetch, mock_json_logger_class):
        """Test collection fails when processing raises exception."""
        from src.data_collection.shelly_em3 import collect_shelly_em3_data

        with patch.dict("os.environ", {"SHELLY_EM3_URL": "http://192.168.1.5"}):
            # Mock fetch to return invalid data (will cause processing error)
            mock_fetch.return_value = {"emeters": []}  # Wrong number of emeters

            # Mock JSON logger
            mock_json_logger = MagicMock()
            mock_json_logger_class.return_value = mock_json_logger

            result = await collect_shelly_em3_data(dry_run=False)

            self.assertEqual(result, 1)  # Failure

    @patch("src.data_collection.shelly_em3.JSONDataLogger")
    @patch("src.data_collection.shelly_em3.write_shelly_em3_to_influx")
    @patch("src.data_collection.shelly_em3.fetch_shelly_em3_status")
    async def test_collect_shelly_em3_data_write_fails(
        self, mock_fetch, mock_write, mock_json_logger_class
    ):
        """Test collection fails when write fails."""
        from src.data_collection.shelly_em3 import collect_shelly_em3_data

        with patch.dict("os.environ", {"SHELLY_EM3_URL": "http://192.168.1.5"}):
            # Mock fetch to succeed
            mock_fetch.return_value = self.sample_status

            # Mock write to fail
            mock_write.return_value = False

            # Mock JSON logger
            mock_json_logger = MagicMock()
            mock_json_logger_class.return_value = mock_json_logger

            result = await collect_shelly_em3_data(dry_run=False)

            self.assertEqual(result, 1)  # Failure

    @patch("asyncio.run")
    @patch("argparse.ArgumentParser.parse_args")
    def test_main(self, mock_parse_args, mock_asyncio_run):
        """Test main entry point."""
        from src.data_collection.shelly_em3 import main

        # Mock arguments
        mock_args = MagicMock()
        mock_args.dry_run = True
        mock_parse_args.return_value = mock_args

        # Mock asyncio.run to return success
        mock_asyncio_run.return_value = 0

        result = main()

        self.assertEqual(result, 0)
        mock_asyncio_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
