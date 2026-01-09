"""Unit tests for wind power data collection."""

import datetime
import os
import sys
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.data_collection.windpower import (
    FINGRID_VARIABLES,
    collect_windpower_data,
    fetch_all_windpower_data,
    fetch_fingrid_data,
    fetch_fmi_windpower_forecast,
    process_windpower_data,
    write_windpower_to_influx,
)


@pytest.fixture
def sample_fingrid_response():
    """Sample Fingrid API response."""
    return [
        {
            "startTime": "2024-01-15T10:00:00.000Z",
            "endTime": "2024-01-15T11:00:00.000Z",
            "value": 1500.5,
        },
        {
            "startTime": "2024-01-15T11:00:00.000Z",
            "endTime": "2024-01-15T12:00:00.000Z",
            "value": 1600.0,
        },
    ]


@pytest.fixture
def sample_fmi_response():
    """Sample FMI forecast response."""
    return {
        "time": {"timezone": "Europe/Helsinki"},
        "series": [
            {
                "data": [
                    [1705315200000, 1.5],  # 2024-01-15 10:00:00 UTC in milliseconds, kW
                    [1705318800000, 1.6],  # 2024-01-15 11:00:00 UTC
                ]
            }
        ],
    }


class TestFetchFingridData:
    """Tests for fetch_fingrid_data function."""

    @pytest.mark.asyncio
    async def test_fetch_fingrid_data_success(self, sample_fingrid_response):
        """Test successful fetch from Fingrid API."""
        start_time = datetime.datetime(2024, 1, 15, 10, 0, 0)
        end_time = datetime.datetime(2024, 1, 15, 12, 0, 0)

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value=sample_fingrid_response)
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            result = await fetch_fingrid_data(75, start_time, end_time)

            assert result == sample_fingrid_response
            assert len(result) == 2

    @pytest.mark.asyncio
    async def test_fetch_fingrid_data_with_dict_response(self, sample_fingrid_response):
        """Test fetch when API returns dict with 'data' key."""
        start_time = datetime.datetime(2024, 1, 15, 10, 0, 0)
        end_time = datetime.datetime(2024, 1, 15, 12, 0, 0)

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"data": sample_fingrid_response})
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            result = await fetch_fingrid_data(75, start_time, end_time)

            assert result == sample_fingrid_response

    @pytest.mark.asyncio
    async def test_fetch_fingrid_data_rate_limited(self, sample_fingrid_response):
        """Test handling of rate limit (429) response."""
        start_time = datetime.datetime(2024, 1, 15, 10, 0, 0)
        end_time = datetime.datetime(2024, 1, 15, 12, 0, 0)

        with patch("aiohttp.ClientSession") as mock_session_class:
            with patch("time.sleep"):  # Mock sleep to speed up test
                # First response is 429
                mock_response_429 = MagicMock()
                mock_response_429.status = 429
                mock_response_429.__aenter__ = AsyncMock(return_value=mock_response_429)
                mock_response_429.__aexit__ = AsyncMock(return_value=None)

                # Second response is 200
                mock_response_200 = MagicMock()
                mock_response_200.status = 200
                mock_response_200.json = AsyncMock(return_value=sample_fingrid_response)
                mock_response_200.__aenter__ = AsyncMock(return_value=mock_response_200)
                mock_response_200.__aexit__ = AsyncMock(return_value=None)

                mock_session = MagicMock()
                mock_session.get = MagicMock(side_effect=[mock_response_429, mock_response_200])
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=None)

                mock_session_class.return_value = mock_session

                result = await fetch_fingrid_data(75, start_time, end_time)

                assert result == sample_fingrid_response

    @pytest.mark.asyncio
    async def test_fetch_fingrid_data_http_error(self):
        """Test handling of HTTP error response."""
        start_time = datetime.datetime(2024, 1, 15, 10, 0, 0)
        end_time = datetime.datetime(2024, 1, 15, 12, 0, 0)

        with patch("aiohttp.ClientSession") as mock_session_class:
            with patch("time.sleep"):  # Mock sleep
                mock_response = AsyncMock()
                mock_response.status = 500
                mock_response.text = AsyncMock(return_value="Internal Server Error")

                mock_session = AsyncMock()
                mock_session.get.return_value.__aenter__.return_value = mock_response
                mock_session_class.return_value.__aenter__.return_value = mock_session

                result = await fetch_fingrid_data(75, start_time, end_time)

                assert result is None

    @pytest.mark.asyncio
    async def test_fetch_fingrid_data_exception(self):
        """Test handling of exception during fetch."""
        start_time = datetime.datetime(2024, 1, 15, 10, 0, 0)
        end_time = datetime.datetime(2024, 1, 15, 12, 0, 0)

        with patch("aiohttp.ClientSession") as mock_session_class:
            with patch("time.sleep"):  # Mock sleep
                mock_session = Mock()
                mock_session.get.side_effect = Exception("Connection error")
                mock_session_class.return_value.__aenter__.return_value = mock_session

                result = await fetch_fingrid_data(75, start_time, end_time)

                assert result is None


class TestFetchFmiWindpowerForecast:
    """Tests for fetch_fmi_windpower_forecast function."""

    @pytest.mark.asyncio
    async def test_fetch_fmi_success(self, sample_fmi_response):
        """Test successful fetch from FMI API."""
        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value=sample_fmi_response)
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            result = await fetch_fmi_windpower_forecast()

            assert result == sample_fmi_response

    @pytest.mark.asyncio
    async def test_fetch_fmi_http_error(self):
        """Test handling of HTTP error from FMI."""
        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_response = AsyncMock()
            mock_response.status = 404
            mock_response.text = AsyncMock(return_value="Not found")

            mock_session = AsyncMock()
            mock_session.get.return_value.__aenter__.return_value = mock_response
            mock_session_class.return_value.__aenter__.return_value = mock_session

            result = await fetch_fmi_windpower_forecast()

            assert result is None

    @pytest.mark.asyncio
    async def test_fetch_fmi_exception(self):
        """Test handling of exception during FMI fetch."""
        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = Mock()
            mock_session.get.side_effect = Exception("Connection error")
            mock_session_class.return_value.__aenter__.return_value = mock_session

            result = await fetch_fmi_windpower_forecast()

            assert result is None


class TestFetchAllWindpowerData:
    """Tests for fetch_all_windpower_data function."""

    @pytest.mark.asyncio
    async def test_fetch_all_success(self, sample_fingrid_response, sample_fmi_response):
        """Test fetching all wind power data."""
        start_time = datetime.datetime(2024, 1, 15, 10, 0, 0)
        end_time = datetime.datetime(2024, 1, 15, 12, 0, 0)

        with patch("src.data_collection.windpower.fetch_fingrid_data") as mock_fetch_fingrid:
            with patch(
                "src.data_collection.windpower.fetch_fmi_windpower_forecast"
            ) as mock_fetch_fmi:
                mock_fetch_fingrid.return_value = sample_fingrid_response
                mock_fetch_fmi.return_value = sample_fmi_response

                result = await fetch_all_windpower_data(start_time, end_time)

                # Should have all Fingrid variables + FMI forecast
                assert len(result) == len(FINGRID_VARIABLES) + 1
                assert "FMI forecast" in result
                # Check that Fingrid was called for each variable
                assert mock_fetch_fingrid.call_count == len(FINGRID_VARIABLES)

    @pytest.mark.asyncio
    async def test_fetch_all_partial_failure(self, sample_fmi_response):
        """Test fetch when some Fingrid variables fail."""
        start_time = datetime.datetime(2024, 1, 15, 10, 0, 0)
        end_time = datetime.datetime(2024, 1, 15, 12, 0, 0)

        with patch("src.data_collection.windpower.fetch_fingrid_data") as mock_fetch_fingrid:
            with patch(
                "src.data_collection.windpower.fetch_fmi_windpower_forecast"
            ) as mock_fetch_fmi:
                # Some Fingrid calls fail (return None)
                mock_fetch_fingrid.return_value = None
                mock_fetch_fmi.return_value = sample_fmi_response

                result = await fetch_all_windpower_data(start_time, end_time)

                # Should still have FMI forecast
                assert "FMI forecast" in result
                # But no Fingrid data
                assert len(result) == 1


class TestProcessWindpowerData:
    """Tests for process_windpower_data function."""

    def test_process_fingrid_data(self, sample_fingrid_response):
        """Test processing Fingrid data."""
        responses = {"Production": sample_fingrid_response}

        result = process_windpower_data(responses)

        # Should have 2 time points
        assert len(result) == 2
        # Check that values are integers (Production field)
        for fields in result.values():
            assert "Production" in fields
            assert isinstance(fields["Production"], int)

    def test_process_fmi_data(self, sample_fmi_response):
        """Test processing FMI forecast data."""
        responses = {"FMI forecast": sample_fmi_response}

        result = process_windpower_data(responses)

        # Should have 2 time points
        assert len(result) == 2
        # Check that values are converted to MW (multiplied by 1000)
        for fields in result.values():
            assert "FMI forecast" in fields
            # 1.5 kW * 1000 = 1500 MW
            assert fields["FMI forecast"] in [1500.0, 1600.0]

    def test_process_mixed_data(self, sample_fingrid_response, sample_fmi_response):
        """Test processing both Fingrid and FMI data."""
        responses = {"Production": sample_fingrid_response, "FMI forecast": sample_fmi_response}

        result = process_windpower_data(responses)

        # Should have 4 time points (Fingrid and FMI have different timestamps)
        assert len(result) == 4
        # Should have both Production and FMI forecast in separate time points
        production_count = sum(1 for fields in result.values() if "Production" in fields)
        fmi_count = sum(1 for fields in result.values() if "FMI forecast" in fields)
        assert production_count == 2
        assert fmi_count == 2

    def test_process_invalid_fmi_data(self):
        """Test processing FMI data with wrong timezone."""
        invalid_fmi = {
            "time": {"timezone": "UTC"},  # Wrong timezone
            "series": [{"data": [[1705315200000, 1.5]]}],
        }
        responses = {"FMI forecast": invalid_fmi}

        # Should still process but log warning
        result = process_windpower_data(responses)

        assert len(result) == 1

    def test_process_empty_responses(self):
        """Test processing empty responses."""
        responses = {}

        result = process_windpower_data(responses)

        assert len(result) == 0


class TestWriteWindpowerToInflux:
    """Tests for write_windpower_to_influx function."""

    @pytest.mark.asyncio
    async def test_write_to_influx_success(self):
        """Test successful write to InfluxDB."""
        processed_data = {
            datetime.datetime(2024, 1, 15, 10, 0, 0): {
                "Production": 1500,
                "FMI forecast": 1600.0,
            }
        }

        with patch("src.data_collection.windpower.get_config") as mock_get_config:
            with patch("src.data_collection.windpower.InfluxClient") as mock_influx_class:
                mock_config = Mock()
                mock_config.influxdb_bucket_windpower = "windpower"
                mock_get_config.return_value = mock_config

                mock_influx = Mock()
                mock_influx.write_api = Mock()
                mock_influx.write_api.write = Mock()
                mock_influx_class.return_value = mock_influx

                with patch("influxdb_client.Point") as mock_point:
                    mock_point_instance = Mock()
                    mock_point_instance.tag.return_value = mock_point_instance
                    mock_point_instance.field.return_value = mock_point_instance
                    mock_point_instance.time.return_value = mock_point_instance
                    mock_point.return_value = mock_point_instance

                    result = await write_windpower_to_influx(processed_data, dry_run=False)

                    assert result is not None
                    assert mock_influx.write_api.write.called

    @pytest.mark.asyncio
    async def test_write_to_influx_dry_run(self):
        """Test write in dry-run mode."""
        processed_data = {datetime.datetime(2024, 1, 15, 10, 0, 0): {"Production": 1500}}

        result = await write_windpower_to_influx(processed_data, dry_run=True)

        # Should return a timestamp but not actually write
        assert result is not None

    @pytest.mark.asyncio
    async def test_write_to_influx_exception(self):
        """Test handling of exception during write."""
        processed_data = {datetime.datetime(2024, 1, 15, 10, 0, 0): {"Production": 1500}}

        with patch("src.data_collection.windpower.get_config"):
            with patch("src.data_collection.windpower.InfluxClient") as mock_influx:
                mock_influx.side_effect = Exception("InfluxDB error")

                result = await write_windpower_to_influx(processed_data, dry_run=False)

                assert result is None


class TestCollectWindpowerData:
    """Tests for collect_windpower_data function."""

    @pytest.mark.asyncio
    async def test_collect_success(self, sample_fingrid_response, sample_fmi_response):
        """Test successful data collection."""
        with patch("src.data_collection.windpower.fetch_all_windpower_data") as mock_fetch:
            with patch("src.data_collection.windpower.write_windpower_to_influx") as mock_write:
                with patch("src.data_collection.windpower.JSONDataLogger") as mock_logger:
                    # Mock successful fetch and write
                    mock_fetch.return_value = {"Production": sample_fingrid_response}
                    mock_write.return_value = datetime.datetime(2024, 1, 15, 10, 0, 0)

                    mock_json_logger = Mock()
                    mock_logger.return_value = mock_json_logger

                    result = await collect_windpower_data(dry_run=False)

                    assert result == 0  # Success
                    mock_fetch.assert_called_once()
                    mock_write.assert_called_once()
                    mock_json_logger.log_data.assert_called_once()
                    mock_json_logger.cleanup_old_logs.assert_called_once()

    @pytest.mark.asyncio
    async def test_collect_no_data_fetched(self):
        """Test collection when no data is fetched."""
        with patch("src.data_collection.windpower.fetch_all_windpower_data") as mock_fetch:
            with patch("src.data_collection.windpower.JSONDataLogger") as mock_logger:
                # Mock empty response
                mock_fetch.return_value = {}

                mock_json_logger = Mock()
                mock_logger.return_value = mock_json_logger

                result = await collect_windpower_data(dry_run=False)

                assert result == 1  # Failure

    @pytest.mark.asyncio
    async def test_collect_write_fails(self, sample_fingrid_response):
        """Test collection when write fails."""
        with patch("src.data_collection.windpower.fetch_all_windpower_data") as mock_fetch:
            with patch("src.data_collection.windpower.write_windpower_to_influx") as mock_write:
                with patch("src.data_collection.windpower.JSONDataLogger") as mock_logger:
                    mock_fetch.return_value = {"Production": sample_fingrid_response}
                    mock_write.return_value = None  # Write failed

                    mock_json_logger = Mock()
                    mock_logger.return_value = mock_json_logger

                    result = await collect_windpower_data(dry_run=False)

                    assert result == 1  # Failure


class TestMain:
    """Tests for main entry point."""

    def test_main(self):
        """Test main entry point."""
        with patch("asyncio.run") as mock_asyncio_run:
            with patch("argparse.ArgumentParser.parse_args") as mock_parse_args:
                from src.data_collection.windpower import main

                mock_args = Mock()
                mock_args.dry_run = True
                mock_args.verbose = False
                mock_args.start_date = None
                mock_args.end_date = None
                mock_parse_args.return_value = mock_args

                mock_asyncio_run.return_value = 0

                result = main()

                assert result == 0
                mock_asyncio_run.assert_called_once()
