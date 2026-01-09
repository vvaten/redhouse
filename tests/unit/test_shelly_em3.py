"""Unit tests for Shelly EM3 data collection."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.data_collection.shelly_em3 import (
    collect_shelly_em3_data,
    fetch_shelly_em3_status,
    process_shelly_em3_data,
    write_shelly_em3_to_influx,
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

    def test_process_shelly_em3_data_negative_net(self):
        """Test that negative net energy is handled (export scenario)."""
        status = {
            "emeters": [
                {
                    "power": -100.0,
                    "current": 0.5,
                    "voltage": 230.0,
                    "pf": 0.95,
                    "total": 1000.0,
                    "total_returned": 2000.0,
                },
                {
                    "power": -50.0,
                    "current": 0.3,
                    "voltage": 230.0,
                    "pf": 0.95,
                    "total": 500.0,
                    "total_returned": 700.0,
                },
                {
                    "power": -75.0,
                    "current": 0.4,
                    "voltage": 230.0,
                    "pf": 0.95,
                    "total": 800.0,
                    "total_returned": 1200.0,
                },
            ]
        }

        fields = process_shelly_em3_data(status)

        # Net can be negative (exporting more than consuming)
        self.assertEqual(fields["phase1_net_total"], -1000.0)  # 1000 - 2000
        self.assertEqual(fields["phase2_net_total"], -200.0)  # 500 - 700
        self.assertEqual(fields["phase3_net_total"], -400.0)  # 800 - 1200
        self.assertEqual(fields["net_total_energy"], -1600.0)  # 2300 - 3900


# Async tests using pytest
@pytest.mark.asyncio
async def test_fetch_shelly_em3_status_success():
    """Test successful fetch of Shelly EM3 status."""
    sample_status = {"emeters": [{"power": 100}] * 3}

    with patch("aiohttp.ClientSession") as mock_session_class:
        # Mock the response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=sample_status)

        # Mock the session context managers
        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value.__aenter__.return_value = mock_session

        # Call the function
        result = await fetch_shelly_em3_status("http://192.168.1.5")

        # Verify the result
        assert result == sample_status
        mock_session.get.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_shelly_em3_status_http_error():
    """Test fetch with HTTP error response."""
    with patch("aiohttp.ClientSession") as mock_session_class:
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
        assert result is None


@pytest.mark.asyncio
async def test_fetch_shelly_em3_status_timeout():
    """Test fetch with timeout."""
    with patch("aiohttp.ClientSession") as mock_session_class:
        # Mock timeout exception
        mock_session = MagicMock()
        mock_session.get.side_effect = asyncio.TimeoutError("Timeout")
        mock_session_class.return_value.__aenter__.return_value = mock_session

        # Call the function
        result = await fetch_shelly_em3_status("http://192.168.1.5")

        # Should return None on timeout
        assert result is None


@pytest.mark.asyncio
async def test_fetch_shelly_em3_status_exception():
    """Test fetch with general exception."""
    with patch("aiohttp.ClientSession") as mock_session_class:
        # Mock general exception
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("Connection error")
        mock_session_class.return_value.__aenter__.return_value = mock_session

        # Call the function
        result = await fetch_shelly_em3_status("http://192.168.1.5")

        # Should return None on exception
        assert result is None


@pytest.mark.asyncio
async def test_write_shelly_em3_to_influx_success():
    """Test successful write to InfluxDB."""
    with patch("src.data_collection.shelly_em3.get_config") as mock_get_config:
        with patch("src.data_collection.shelly_em3.InfluxClient") as mock_influx_class:
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

            assert result is True
            mock_influx.write_point.assert_called_once()


@pytest.mark.asyncio
async def test_write_shelly_em3_to_influx_dry_run():
    """Test write in dry-run mode."""
    fields = {"total_power": 545.6}

    result = await write_shelly_em3_to_influx(fields, dry_run=True)

    assert result is True
    # Should not call InfluxDB in dry-run mode


@pytest.mark.asyncio
async def test_write_shelly_em3_to_influx_exception():
    """Test write handles exceptions."""
    with patch("src.data_collection.shelly_em3.get_config") as mock_get_config:
        with patch("src.data_collection.shelly_em3.InfluxClient") as mock_influx_class:
            # Mock config
            mock_config = MagicMock()
            mock_config.influxdb_bucket_shelly_em3_raw = "shelly_em3_raw"
            mock_get_config.return_value = mock_config

            # Mock InfluxClient to raise exception
            mock_influx_class.side_effect = Exception("Connection error")

            fields = {"total_power": 545.6}

            result = await write_shelly_em3_to_influx(fields, dry_run=False)

            assert result is False


@pytest.mark.asyncio
async def test_collect_shelly_em3_data_success():
    """Test successful data collection."""
    sample_status = {
        "emeters": [
            {
                "power": 100.0,
                "current": 0.5,
                "voltage": 230.0,
                "pf": 0.95,
                "total": 1000.0,
                "total_returned": 100.0,
            }
        ]
        * 3
    }

    with patch.dict("os.environ", {"SHELLY_EM3_URL": "http://192.168.1.5"}):
        with patch("src.data_collection.shelly_em3.fetch_shelly_em3_status") as mock_fetch:
            with patch("src.data_collection.shelly_em3.write_shelly_em3_to_influx") as mock_write:
                with patch(
                    "src.data_collection.shelly_em3.JSONDataLogger"
                ) as mock_json_logger_class:
                    # Mock fetch to return valid data
                    mock_fetch.return_value = sample_status

                    # Mock write to succeed
                    mock_write.return_value = True

                    # Mock JSON logger
                    mock_json_logger = MagicMock()
                    mock_json_logger_class.return_value = mock_json_logger

                    result = await collect_shelly_em3_data(dry_run=False)

                    assert result == 0  # Success
                    mock_fetch.assert_called_once_with("http://192.168.1.5")
                    mock_write.assert_called_once()
                    mock_json_logger.log_data.assert_called_once()
                    mock_json_logger.cleanup_old_logs.assert_called_once()


@pytest.mark.asyncio
async def test_collect_shelly_em3_data_no_url():
    """Test collection fails when SHELLY_EM3_URL not set."""
    # Ensure env var is not set
    with patch.dict("os.environ", {}, clear=True):
        result = await collect_shelly_em3_data(dry_run=False)

        assert result == 1  # Failure


@pytest.mark.asyncio
async def test_collect_shelly_em3_data_fetch_fails():
    """Test collection fails when fetch returns None."""
    with patch.dict("os.environ", {"SHELLY_EM3_URL": "http://192.168.1.5"}):
        with patch("src.data_collection.shelly_em3.fetch_shelly_em3_status") as mock_fetch:
            # Mock fetch to fail
            mock_fetch.return_value = None

            result = await collect_shelly_em3_data(dry_run=False)

            assert result == 1  # Failure


@pytest.mark.asyncio
async def test_collect_shelly_em3_data_process_fails():
    """Test collection fails when processing raises exception."""
    with patch.dict("os.environ", {"SHELLY_EM3_URL": "http://192.168.1.5"}):
        with patch("src.data_collection.shelly_em3.fetch_shelly_em3_status") as mock_fetch:
            with patch("src.data_collection.shelly_em3.JSONDataLogger") as mock_json_logger_class:
                # Mock fetch to return invalid data (will cause processing error)
                mock_fetch.return_value = {"emeters": []}  # Wrong number of emeters

                # Mock JSON logger
                mock_json_logger = MagicMock()
                mock_json_logger_class.return_value = mock_json_logger

                result = await collect_shelly_em3_data(dry_run=False)

                assert result == 1  # Failure


@pytest.mark.asyncio
async def test_collect_shelly_em3_data_write_fails():
    """Test collection fails when write fails."""
    sample_status = {
        "emeters": [
            {
                "power": 100.0,
                "current": 0.5,
                "voltage": 230.0,
                "pf": 0.95,
                "total": 1000.0,
                "total_returned": 100.0,
            }
        ]
        * 3
    }

    with patch.dict("os.environ", {"SHELLY_EM3_URL": "http://192.168.1.5"}):
        with patch("src.data_collection.shelly_em3.fetch_shelly_em3_status") as mock_fetch:
            with patch("src.data_collection.shelly_em3.write_shelly_em3_to_influx") as mock_write:
                with patch(
                    "src.data_collection.shelly_em3.JSONDataLogger"
                ) as mock_json_logger_class:
                    # Mock fetch to succeed
                    mock_fetch.return_value = sample_status

                    # Mock write to fail
                    mock_write.return_value = False

                    # Mock JSON logger
                    mock_json_logger = MagicMock()
                    mock_json_logger_class.return_value = mock_json_logger

                    result = await collect_shelly_em3_data(dry_run=False)

                    assert result == 1  # Failure


@pytest.mark.asyncio
async def test_collect_shelly_em3_data_dry_run():
    """Test collection in dry-run mode."""
    sample_status = {
        "emeters": [
            {
                "power": 100.0,
                "current": 0.5,
                "voltage": 230.0,
                "pf": 0.95,
                "total": 1000.0,
                "total_returned": 100.0,
            }
        ]
        * 3
    }

    with patch.dict("os.environ", {"SHELLY_EM3_URL": "http://192.168.1.5"}):
        with patch("src.data_collection.shelly_em3.fetch_shelly_em3_status") as mock_fetch:
            with patch("src.data_collection.shelly_em3.write_shelly_em3_to_influx") as mock_write:
                with patch(
                    "src.data_collection.shelly_em3.JSONDataLogger"
                ) as mock_json_logger_class:
                    # Mock fetch to return valid data
                    mock_fetch.return_value = sample_status

                    # Mock write to succeed
                    mock_write.return_value = True

                    # Mock JSON logger
                    mock_json_logger = MagicMock()
                    mock_json_logger_class.return_value = mock_json_logger

                    result = await collect_shelly_em3_data(dry_run=True)

                    assert result == 0  # Success
                    # Should pass dry_run=True to write function
                    mock_write.assert_called_once()
                    call_args = mock_write.call_args
                    assert call_args[1]["dry_run"] is True


def test_main():
    """Test main entry point."""
    with patch("asyncio.run") as mock_asyncio_run:
        with patch("argparse.ArgumentParser.parse_args") as mock_parse_args:
            from src.data_collection.shelly_em3 import main

            # Mock arguments
            mock_args = MagicMock()
            mock_args.dry_run = True
            mock_parse_args.return_value = mock_args

            # Mock asyncio.run to return success
            mock_asyncio_run.return_value = 0

            result = main()

            assert result == 0
            mock_asyncio_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
