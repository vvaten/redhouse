"""Extended unit tests for CheckWatt data collection - pytest style."""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.data_collection.checkwatt import (
    collect_checkwatt_data,
    fetch_checkwatt_data,
    main,
    write_checkwatt_to_influx,
)


@pytest.fixture
def sample_checkwatt_data():
    """Sample processed CheckWatt data points."""
    return [
        {
            "epoch_timestamp": 1705315200,  # 2024-01-15 10:00:00
            "Battery_SoC": 50.0,
            "BatteryCharge": 100.0,
            "BatteryDischarge": 0.0,
            "EnergyImport": 200.0,
            "EnergyExport": 50.0,
            "SolarYield": 300.0,
        },
        {
            "epoch_timestamp": 1705315260,  # 2024-01-15 10:01:00
            "Battery_SoC": 51.0,
            "BatteryCharge": 110.0,
            "BatteryDischarge": 0.0,
            "EnergyImport": 210.0,
            "EnergyExport": 55.0,
            "SolarYield": 310.0,
        },
        {
            "epoch_timestamp": 1705315320,  # 2024-01-15 10:02:00 (last record, delta incomplete)
            "Battery_SoC": 52.0,
        },
    ]


@pytest.fixture
def sample_api_response():
    """Sample API response from CheckWatt with 12 measurements."""
    # Create 12 measurements (enough to pass the 10 point minimum)
    measurements = []
    for i in range(12):
        measurements.append({"Value": 50.0 + i})

    return {
        "Grouping": "delta",
        "DateFrom": "2024-01-15T10:00:00",
        "DateTo": "2024-01-15T10:12:00",
        "Meters": [
            {"Measurements": [{"Value": 50.0 + i} for i in range(12)]},  # Battery_SoC
            {"Measurements": [{"Value": 100.0 + i * 10} for i in range(12)]},  # BatteryCharge
            {"Measurements": [{"Value": 0.0} for i in range(12)]},  # BatteryDischarge
            {"Measurements": [{"Value": 200.0 + i * 10} for i in range(12)]},  # EnergyImport
            {"Measurements": [{"Value": 50.0 + i * 5} for i in range(12)]},  # EnergyExport
            {"Measurements": [{"Value": 300.0 + i * 10} for i in range(12)]},  # SolarYield
        ],
    }


class TestFetchCheckwattData:
    """Tests for fetch_checkwatt_data function."""

    @pytest.mark.asyncio
    async def test_fetch_success(self, sample_api_response):
        """Test successful data fetch."""
        auth_token = "test_token"
        meter_ids = ["meter1", "meter2", "meter3", "meter4", "meter5", "meter6"]
        from_date = "2024-01-15T10:00:00"
        to_date = "2024-01-15T11:00:00"

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value=sample_api_response)
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            result = await fetch_checkwatt_data(auth_token, meter_ids, from_date, to_date)

            assert result == sample_api_response
            # Verify URL construction
            mock_session.get.assert_called_once()
            call_args = mock_session.get.call_args
            assert "meterId=meter1" in call_args[0][0]
            assert "grouping=delta" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_fetch_http_error(self):
        """Test fetch with HTTP error."""
        auth_token = "test_token"
        meter_ids = ["meter1"]
        from_date = "2024-01-15T10:00:00"
        to_date = "2024-01-15T11:00:00"

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status = 401
            mock_response.text = AsyncMock(return_value="Unauthorized")
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            result = await fetch_checkwatt_data(auth_token, meter_ids, from_date, to_date)

            assert result is None

    @pytest.mark.asyncio
    async def test_fetch_exception(self):
        """Test fetch with exception."""
        auth_token = "test_token"
        meter_ids = ["meter1"]
        from_date = "2024-01-15T10:00:00"
        to_date = "2024-01-15T11:00:00"

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = MagicMock()
            mock_session.get = MagicMock(side_effect=Exception("Connection error"))
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            result = await fetch_checkwatt_data(auth_token, meter_ids, from_date, to_date)

            assert result is None


class TestWriteCheckwattToInflux:
    """Tests for write_checkwatt_to_influx function."""

    @pytest.mark.asyncio
    async def test_write_success(self, sample_checkwatt_data):
        """Test successful write to InfluxDB."""
        with patch("src.data_collection.checkwatt.get_config") as mock_config:
            with patch("src.data_collection.checkwatt.InfluxClient") as mock_influx_class:
                mock_cfg = Mock()
                mock_cfg.influxdb_bucket_checkwatt = "checkwatt"
                mock_cfg.influxdb_org = "test_org"
                mock_config.return_value = mock_cfg

                mock_influx = Mock()
                mock_influx.write_api = Mock()
                mock_influx.write_api.write = Mock()
                mock_influx_class.return_value = mock_influx

                with patch("influxdb_client.Point") as mock_point_class:
                    mock_point = Mock()
                    mock_point.field = Mock(return_value=mock_point)
                    mock_point.time = Mock(return_value=mock_point)
                    mock_point_class.return_value = mock_point

                    result = await write_checkwatt_to_influx(sample_checkwatt_data, dry_run=False)

                    assert result is True
                    mock_influx.write_api.write.assert_called_once()
                    # 3 points should be written
                    assert mock_point_class.call_count == 3

    @pytest.mark.asyncio
    async def test_write_dry_run(self, sample_checkwatt_data):
        """Test dry-run mode."""
        with patch("src.data_collection.checkwatt.get_config") as mock_config:
            mock_cfg = Mock()
            mock_cfg.influxdb_bucket_checkwatt = "checkwatt"
            mock_config.return_value = mock_cfg

            result = await write_checkwatt_to_influx(sample_checkwatt_data, dry_run=True)

            assert result is True

    @pytest.mark.asyncio
    async def test_write_empty_data(self):
        """Test write with empty data."""
        result = await write_checkwatt_to_influx([], dry_run=False)

        assert result is False

    @pytest.mark.asyncio
    async def test_write_exception(self, sample_checkwatt_data):
        """Test write with exception."""
        with patch("src.data_collection.checkwatt.get_config") as mock_config:
            with patch("src.data_collection.checkwatt.InfluxClient") as mock_influx_class:
                mock_cfg = Mock()
                mock_cfg.influxdb_bucket_checkwatt = "checkwatt"
                mock_config.return_value = mock_cfg

                mock_influx_class.side_effect = Exception("InfluxDB connection error")

                result = await write_checkwatt_to_influx(sample_checkwatt_data, dry_run=False)

                assert result is False


class TestCollectCheckwattData:
    """Tests for collect_checkwatt_data function."""

    @pytest.mark.asyncio
    async def test_collect_success(self, sample_api_response):
        """Test successful data collection."""
        with patch("src.data_collection.checkwatt.get_config") as mock_config:
            with patch("src.data_collection.checkwatt.get_auth_token") as mock_auth:
                with patch("src.data_collection.checkwatt.fetch_checkwatt_data") as mock_fetch:
                    with patch(
                        "src.data_collection.checkwatt.write_checkwatt_to_influx"
                    ) as mock_write:
                        with patch("src.data_collection.checkwatt.JSONDataLogger"):
                            mock_cfg = Mock()
                            mock_cfg.get = Mock(
                                side_effect=lambda key: {
                                    "checkwatt_username": "user@example.com",
                                    "checkwatt_password": "password",
                                    "checkwatt_meter_ids": "m1,m2,m3,m4,m5,m6",
                                }[key]
                            )
                            mock_config.return_value = mock_cfg

                            mock_auth.return_value = "test_token"
                            mock_fetch.return_value = sample_api_response
                            mock_write.return_value = True

                            result = await collect_checkwatt_data(
                                start_date="2024-01-15T10:00:00",
                                end_date="2024-01-15T11:00:00",
                                dry_run=False,
                            )

                            assert result == 0
                            mock_auth.assert_called_once_with("user@example.com", "password")
                            mock_write.assert_called_once()

    @pytest.mark.asyncio
    async def test_collect_missing_credentials(self):
        """Test collection with missing credentials."""
        with patch("src.data_collection.checkwatt.get_config") as mock_config:
            mock_cfg = Mock()
            mock_cfg.get = Mock(return_value=None)
            mock_config.return_value = mock_cfg

            result = await collect_checkwatt_data()

            assert result == 1

    @pytest.mark.asyncio
    async def test_collect_missing_meter_ids(self):
        """Test collection with missing meter IDs."""
        with patch("src.data_collection.checkwatt.get_config") as mock_config:
            mock_cfg = Mock()
            mock_cfg.get = Mock(
                side_effect=lambda key: {
                    "checkwatt_username": "user@example.com",
                    "checkwatt_password": "password",
                    "checkwatt_meter_ids": None,
                }[key]
            )
            mock_config.return_value = mock_cfg

            result = await collect_checkwatt_data()

            assert result == 1

    @pytest.mark.asyncio
    async def test_collect_auth_failure(self):
        """Test collection with auth failure."""
        with patch("src.data_collection.checkwatt.get_config") as mock_config:
            with patch("src.data_collection.checkwatt.get_auth_token") as mock_auth:
                mock_cfg = Mock()
                mock_cfg.get = Mock(
                    side_effect=lambda key: {
                        "checkwatt_username": "user@example.com",
                        "checkwatt_password": "password",
                        "checkwatt_meter_ids": "m1,m2,m3,m4,m5,m6",
                    }[key]
                )
                mock_config.return_value = mock_cfg

                mock_auth.return_value = None

                result = await collect_checkwatt_data()

                assert result == 1

    @pytest.mark.asyncio
    async def test_collect_fetch_failure(self):
        """Test collection with fetch failure."""
        with patch("src.data_collection.checkwatt.get_config") as mock_config:
            with patch("src.data_collection.checkwatt.get_auth_token") as mock_auth:
                with patch("src.data_collection.checkwatt.fetch_checkwatt_data") as mock_fetch:
                    mock_cfg = Mock()
                    mock_cfg.get = Mock(
                        side_effect=lambda key: {
                            "checkwatt_username": "user@example.com",
                            "checkwatt_password": "password",
                            "checkwatt_meter_ids": "m1,m2,m3,m4,m5,m6",
                        }[key]
                    )
                    mock_config.return_value = mock_cfg

                    mock_auth.return_value = "test_token"
                    mock_fetch.return_value = None

                    result = await collect_checkwatt_data()

                    assert result == 1

    @pytest.mark.asyncio
    async def test_collect_invalid_response_format(self):
        """Test collection with invalid response format."""
        with patch("src.data_collection.checkwatt.get_config") as mock_config:
            with patch("src.data_collection.checkwatt.get_auth_token") as mock_auth:
                with patch("src.data_collection.checkwatt.fetch_checkwatt_data") as mock_fetch:
                    with patch("src.data_collection.checkwatt.JSONDataLogger"):
                        mock_cfg = Mock()
                        mock_cfg.get = Mock(
                            side_effect=lambda key: {
                                "checkwatt_username": "user@example.com",
                                "checkwatt_password": "password",
                                "checkwatt_meter_ids": "m1,m2,m3,m4,m5,m6",
                            }[key]
                        )
                        mock_config.return_value = mock_cfg

                        mock_auth.return_value = "test_token"
                        # Response has wrong number of fields
                        mock_fetch.return_value = {"field1": "value1"}

                        result = await collect_checkwatt_data()

                        assert result == 1

    @pytest.mark.asyncio
    async def test_collect_processing_error(self, sample_api_response):
        """Test collection with processing error."""
        with patch("src.data_collection.checkwatt.get_config") as mock_config:
            with patch("src.data_collection.checkwatt.get_auth_token") as mock_auth:
                with patch("src.data_collection.checkwatt.fetch_checkwatt_data") as mock_fetch:
                    with patch(
                        "src.data_collection.checkwatt.process_checkwatt_data"
                    ) as mock_process:
                        with patch("src.data_collection.checkwatt.JSONDataLogger"):
                            mock_cfg = Mock()
                            mock_cfg.get = Mock(
                                side_effect=lambda key: {
                                    "checkwatt_username": "user@example.com",
                                    "checkwatt_password": "password",
                                    "checkwatt_meter_ids": "m1,m2,m3,m4,m5,m6",
                                }[key]
                            )
                            mock_config.return_value = mock_cfg

                            mock_auth.return_value = "test_token"
                            mock_fetch.return_value = sample_api_response
                            mock_process.side_effect = Exception("Processing error")

                            result = await collect_checkwatt_data()

                            assert result == 1

    @pytest.mark.asyncio
    async def test_collect_too_little_data(self, sample_api_response):
        """Test collection with too little data."""
        with patch("src.data_collection.checkwatt.get_config") as mock_config:
            with patch("src.data_collection.checkwatt.get_auth_token") as mock_auth:
                with patch("src.data_collection.checkwatt.fetch_checkwatt_data") as mock_fetch:
                    with patch(
                        "src.data_collection.checkwatt.process_checkwatt_data"
                    ) as mock_process:
                        with patch("src.data_collection.checkwatt.JSONDataLogger"):
                            mock_cfg = Mock()
                            mock_cfg.get = Mock(
                                side_effect=lambda key: {
                                    "checkwatt_username": "user@example.com",
                                    "checkwatt_password": "password",
                                    "checkwatt_meter_ids": "m1,m2,m3,m4,m5,m6",
                                }[key]
                            )
                            mock_config.return_value = mock_cfg

                            mock_auth.return_value = "test_token"
                            mock_fetch.return_value = sample_api_response
                            # Return too little data (less than 10 points)
                            mock_process.return_value = [{"epoch_timestamp": 1705315200}]

                            result = await collect_checkwatt_data()

                            assert result == 1

    @pytest.mark.asyncio
    async def test_collect_write_failure(self, sample_api_response):
        """Test collection with write failure."""
        with patch("src.data_collection.checkwatt.get_config") as mock_config:
            with patch("src.data_collection.checkwatt.get_auth_token") as mock_auth:
                with patch("src.data_collection.checkwatt.fetch_checkwatt_data") as mock_fetch:
                    with patch(
                        "src.data_collection.checkwatt.write_checkwatt_to_influx"
                    ) as mock_write:
                        with patch("src.data_collection.checkwatt.JSONDataLogger"):
                            mock_cfg = Mock()
                            mock_cfg.get = Mock(
                                side_effect=lambda key: {
                                    "checkwatt_username": "user@example.com",
                                    "checkwatt_password": "password",
                                    "checkwatt_meter_ids": "m1,m2,m3,m4,m5,m6",
                                }[key]
                            )
                            mock_config.return_value = mock_cfg

                            mock_auth.return_value = "test_token"
                            mock_fetch.return_value = sample_api_response
                            mock_write.return_value = False

                            result = await collect_checkwatt_data()

                            assert result == 1

    @pytest.mark.asyncio
    async def test_collect_last_hour_only(self, sample_api_response):
        """Test collection with last_hour_only flag."""
        with patch("src.data_collection.checkwatt.get_config") as mock_config:
            with patch("src.data_collection.checkwatt.get_auth_token") as mock_auth:
                with patch("src.data_collection.checkwatt.fetch_checkwatt_data") as mock_fetch:
                    with patch(
                        "src.data_collection.checkwatt.write_checkwatt_to_influx"
                    ) as mock_write:
                        with patch("src.data_collection.checkwatt.JSONDataLogger"):
                            mock_cfg = Mock()
                            mock_cfg.get = Mock(
                                side_effect=lambda key: {
                                    "checkwatt_username": "user@example.com",
                                    "checkwatt_password": "password",
                                    "checkwatt_meter_ids": "m1,m2,m3,m4,m5,m6",
                                }[key]
                            )
                            mock_config.return_value = mock_cfg

                            mock_auth.return_value = "test_token"
                            mock_fetch.return_value = sample_api_response
                            mock_write.return_value = True

                            result = await collect_checkwatt_data(last_hour_only=True)

                            assert result == 0
                            # Verify that fetch was called with dates calculated from last hour
                            mock_fetch.assert_called_once()


class TestMain:
    """Tests for main entry point."""

    def test_main_success(self):
        """Test main with successful collection."""
        with patch("sys.argv", ["checkwatt.py", "--dry-run"]):
            with patch("src.data_collection.checkwatt.collect_checkwatt_data"):
                with patch("asyncio.run") as mock_asyncio_run:
                    mock_asyncio_run.return_value = 0

                    result = main()

                    assert result == 0

    def test_main_with_last_hour(self):
        """Test main with --last-hour flag."""
        with patch("sys.argv", ["checkwatt.py", "--last-hour"]):
            with patch("src.data_collection.checkwatt.collect_checkwatt_data"):
                with patch("asyncio.run") as mock_asyncio_run:
                    mock_asyncio_run.return_value = 0

                    result = main()

                    assert result == 0

    def test_main_with_dates(self):
        """Test main with explicit dates."""
        with patch(
            "sys.argv",
            [
                "checkwatt.py",
                "--start-date",
                "2024-01-15T10:00:00",
                "--end-date",
                "2024-01-15T11:00:00",
            ],
        ):
            with patch("src.data_collection.checkwatt.collect_checkwatt_data"):
                with patch("asyncio.run") as mock_asyncio_run:
                    mock_asyncio_run.return_value = 0

                    result = main()

                    assert result == 0

    def test_main_verbose(self):
        """Test main with --verbose flag."""
        with patch("sys.argv", ["checkwatt.py", "--verbose", "--dry-run"]):
            with patch("src.data_collection.checkwatt.collect_checkwatt_data"):
                with patch("asyncio.run") as mock_asyncio_run:
                    mock_asyncio_run.return_value = 0

                    result = main()

                    assert result == 0

    def test_main_failure(self):
        """Test main with collection failure."""
        with patch("sys.argv", ["checkwatt.py"]):
            with patch("asyncio.run") as mock_asyncio_run:
                mock_asyncio_run.return_value = 1

                result = main()

                assert result == 1

    def test_main_exception(self):
        """Test main with unhandled exception."""
        with patch("sys.argv", ["checkwatt.py"]):
            with patch("asyncio.run") as mock_asyncio_run:
                mock_asyncio_run.side_effect = Exception("Unhandled error")

                result = main()

                assert result == 1
