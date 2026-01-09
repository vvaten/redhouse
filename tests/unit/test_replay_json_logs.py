"""Unit tests for JSON log replay utility."""

import datetime
import os
import sys
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.tools.replay_json_logs import list_available_logs, main, replay_log_file, replay_logs


@pytest.fixture
def mock_log_dir(tmp_path):
    """Create a temporary log directory structure."""
    log_dir = tmp_path / "data_logs"
    log_dir.mkdir()

    spot_prices_dir = log_dir / "spot_prices"
    spot_prices_dir.mkdir()
    (spot_prices_dir / "log_2024-01-15_10-00-00.json").write_text("{}")
    (spot_prices_dir / "log_2024-01-14_10-00-00.json").write_text("{}")

    checkwatt_dir = log_dir / "checkwatt"
    checkwatt_dir.mkdir()
    (checkwatt_dir / "log_2024-01-15_12-00-00.json").write_text("{}")

    return log_dir


@pytest.fixture
def sample_log_entry():
    """Sample log entry data."""
    return {
        "timestamp": "2024-01-15T10:00:00",
        "data": {"test": "data"},
        "metadata": {"source": "test"},
    }


class TestListAvailableLogs:
    """Tests for list_available_logs function."""

    def test_list_all_logs(self, tmp_path, monkeypatch):
        """Test listing logs for all data sources."""
        log_dir = tmp_path / "data_logs"
        log_dir.mkdir()

        source1_dir = log_dir / "source1"
        source1_dir.mkdir()
        (source1_dir / "log1.json").write_text("{}")

        source2_dir = log_dir / "source2"
        source2_dir.mkdir()
        (source2_dir / "log2.json").write_text("{}")

        monkeypatch.chdir(tmp_path)

        result = list_available_logs()

        assert "source1" in result
        assert "source2" in result
        assert len(result["source1"]) >= 1
        assert len(result["source2"]) >= 1

    def test_list_specific_source(self, tmp_path, monkeypatch):
        """Test listing logs for specific data source."""
        log_dir = tmp_path / "data_logs"
        log_dir.mkdir()

        source_dir = log_dir / "test_source"
        source_dir.mkdir()
        (source_dir / "log1.json").write_text("{}")
        (source_dir / "log2.json").write_text("{}")

        monkeypatch.chdir(tmp_path)

        result = list_available_logs(data_source="test_source", days=30)

        assert "test_source" in result
        assert len(result["test_source"]) >= 2

    def test_list_no_log_dir(self, tmp_path, monkeypatch):
        """Test listing when log directory doesn't exist."""
        monkeypatch.chdir(tmp_path)

        result = list_available_logs()

        assert result == {}

    def test_list_no_logs_in_source(self, tmp_path, monkeypatch):
        """Test listing when data source has no logs."""
        log_dir = tmp_path / "data_logs"
        log_dir.mkdir()

        empty_dir = log_dir / "empty_source"
        empty_dir.mkdir()

        monkeypatch.chdir(tmp_path)

        result = list_available_logs(data_source="empty_source")

        assert result == {}


class TestReplayLogFile:
    """Tests for replay_log_file function."""

    @pytest.mark.asyncio
    async def test_replay_spot_prices_success(self, tmp_path):
        """Test successful replay of spot prices log."""
        log_file = tmp_path / "test.json"

        with patch("src.tools.replay_json_logs.JSONDataLogger") as mock_logger_class:
            mock_logger = Mock()
            mock_logger.load_log.return_value = {
                "timestamp": "2024-01-15T10:00:00",
                "data": {"test": "data"},
                "metadata": {},
            }
            mock_logger_class.return_value = mock_logger

            with patch("src.common.config.get_config"):
                with patch("src.data_collection.spot_prices.process_spot_prices") as mock_process:
                    with patch(
                        "src.data_collection.spot_prices.write_spot_prices_to_influx"
                    ) as mock_write:
                        mock_process.return_value = {"processed": "data"}
                        mock_write.return_value = datetime.datetime(2024, 1, 15, 10, 0, 0)

                        result = await replay_log_file(log_file, "spot_prices", dry_run=False)

                        assert result is True
                        mock_process.assert_called_once()
                        mock_write.assert_called_once()

    @pytest.mark.asyncio
    async def test_replay_checkwatt_success(self, tmp_path):
        """Test successful replay of checkwatt log."""
        log_file = tmp_path / "test.json"

        with patch("src.tools.replay_json_logs.JSONDataLogger") as mock_logger_class:
            mock_logger = Mock()
            mock_logger.load_log.return_value = {
                "timestamp": "2024-01-15T10:00:00",
                "data": {"test": "data"},
                "metadata": {},
            }
            mock_logger_class.return_value = mock_logger

            with patch("src.data_collection.checkwatt.process_checkwatt_data") as mock_process:
                with patch("src.data_collection.checkwatt.write_checkwatt_to_influx") as mock_write:
                    mock_process.return_value = {"processed": "data"}
                    mock_write.return_value = True

                    result = await replay_log_file(log_file, "checkwatt", dry_run=False)

                    assert result is True
                    mock_process.assert_called_once()
                    mock_write.assert_called_once()

    @pytest.mark.asyncio
    async def test_replay_weather_success(self, tmp_path):
        """Test successful replay of weather log."""
        log_file = tmp_path / "test.json"

        with patch("src.tools.replay_json_logs.JSONDataLogger") as mock_logger_class:
            mock_logger = Mock()
            mock_logger.load_log.return_value = {
                "timestamp": "2024-01-15T10:00:00",
                "data": {"2024-01-15T10:00:00": {"temp": 20}},
                "metadata": {},
            }
            mock_logger_class.return_value = mock_logger

            with patch("src.data_collection.weather.write_weather_to_influx") as mock_write:
                mock_write.return_value = True

                result = await replay_log_file(log_file, "weather", dry_run=False)

                assert result is True
                mock_write.assert_called_once()

    @pytest.mark.asyncio
    async def test_replay_windpower_success(self, tmp_path):
        """Test successful replay of windpower log."""
        log_file = tmp_path / "test.json"

        with patch("src.tools.replay_json_logs.JSONDataLogger") as mock_logger_class:
            mock_logger = Mock()
            mock_logger.load_log.return_value = {
                "timestamp": "2024-01-15T10:00:00",
                "data": {"Production": [{"value": 1500}]},
                "metadata": {},
            }
            mock_logger_class.return_value = mock_logger

            with patch("src.data_collection.windpower.process_windpower_data") as mock_process:
                with patch("src.data_collection.windpower.write_windpower_to_influx") as mock_write:
                    mock_process.return_value = {"processed": "data"}
                    mock_write.return_value = datetime.datetime(2024, 1, 15, 10, 0, 0)

                    result = await replay_log_file(log_file, "windpower", dry_run=False)

                    assert result is True
                    mock_process.assert_called_once()
                    mock_write.assert_called_once()

    @pytest.mark.asyncio
    async def test_replay_temperature_success(self, tmp_path):
        """Test successful replay of temperature log."""
        log_file = tmp_path / "test.json"

        with patch("src.tools.replay_json_logs.JSONDataLogger") as mock_logger_class:
            mock_logger = Mock()
            mock_logger.load_log.return_value = {
                "timestamp": "2024-01-15T10:00:00",
                "data": {"sensor1": 22.5},
                "metadata": {},
            }
            mock_logger_class.return_value = mock_logger

            with patch(
                "src.data_collection.temperature.write_temperatures_to_influx"
            ) as mock_write:
                mock_write.return_value = True

                result = await replay_log_file(log_file, "temperature", dry_run=False)

                assert result is True
                mock_write.assert_called_once()

    @pytest.mark.asyncio
    async def test_replay_shelly_em3_success(self, tmp_path):
        """Test successful replay of shelly_em3 log."""
        log_file = tmp_path / "test.json"

        with patch("src.tools.replay_json_logs.JSONDataLogger") as mock_logger_class:
            mock_logger = Mock()
            mock_logger.load_log.return_value = {
                "timestamp": "2024-01-15T10:00:00",
                "data": {"emeters": [{"power": 100}]},
                "metadata": {"device_url": "http://192.168.1.100"},
            }
            mock_logger_class.return_value = mock_logger

            with patch("src.data_collection.shelly_em3.process_shelly_em3_data") as mock_process:
                with patch(
                    "src.data_collection.shelly_em3.write_shelly_em3_to_influx"
                ) as mock_write:
                    mock_process.return_value = {"processed": "data"}
                    mock_write.return_value = True

                    result = await replay_log_file(log_file, "shelly_em3", dry_run=False)

                    assert result is True
                    mock_process.assert_called_once()
                    mock_write.assert_called_once()

    @pytest.mark.asyncio
    async def test_replay_shelly_em3_no_data(self, tmp_path):
        """Test replay of shelly_em3 log with no data."""
        log_file = tmp_path / "test.json"

        with patch("src.tools.replay_json_logs.JSONDataLogger") as mock_logger_class:
            mock_logger = Mock()
            mock_logger.load_log.return_value = {
                "timestamp": "2024-01-15T10:00:00",
                "data": None,
                "metadata": {},
            }
            mock_logger_class.return_value = mock_logger

            result = await replay_log_file(log_file, "shelly_em3", dry_run=False)

            assert result is False

    @pytest.mark.asyncio
    async def test_replay_dry_run(self, tmp_path):
        """Test replay in dry-run mode."""
        log_file = tmp_path / "test.json"

        with patch("src.tools.replay_json_logs.JSONDataLogger") as mock_logger_class:
            mock_logger = Mock()
            mock_logger.load_log.return_value = {
                "timestamp": "2024-01-15T10:00:00",
                "data": {"test": "data"},
                "metadata": {},
            }
            mock_logger_class.return_value = mock_logger

            result = await replay_log_file(log_file, "spot_prices", dry_run=True)

            assert result is True

    @pytest.mark.asyncio
    async def test_replay_failed_to_load(self, tmp_path):
        """Test replay when log file can't be loaded."""
        log_file = tmp_path / "test.json"

        with patch("src.tools.replay_json_logs.JSONDataLogger") as mock_logger_class:
            mock_logger = Mock()
            mock_logger.load_log.return_value = None
            mock_logger_class.return_value = mock_logger

            result = await replay_log_file(log_file, "spot_prices", dry_run=False)

            assert result is False

    @pytest.mark.asyncio
    async def test_replay_unknown_source(self, tmp_path):
        """Test replay with unknown data source."""
        log_file = tmp_path / "test.json"

        with patch("src.tools.replay_json_logs.JSONDataLogger") as mock_logger_class:
            mock_logger = Mock()
            mock_logger.load_log.return_value = {
                "timestamp": "2024-01-15T10:00:00",
                "data": {"test": "data"},
                "metadata": {},
            }
            mock_logger_class.return_value = mock_logger

            result = await replay_log_file(log_file, "unknown_source", dry_run=False)

            assert result is False

    @pytest.mark.asyncio
    async def test_replay_write_fails(self, tmp_path):
        """Test replay when write to InfluxDB fails."""
        log_file = tmp_path / "test.json"

        with patch("src.tools.replay_json_logs.JSONDataLogger") as mock_logger_class:
            mock_logger = Mock()
            mock_logger.load_log.return_value = {
                "timestamp": "2024-01-15T10:00:00",
                "data": {"test": "data"},
                "metadata": {},
            }
            mock_logger_class.return_value = mock_logger

            with patch("src.data_collection.checkwatt.process_checkwatt_data") as mock_process:
                with patch("src.data_collection.checkwatt.write_checkwatt_to_influx") as mock_write:
                    mock_process.return_value = {"processed": "data"}
                    mock_write.return_value = False

                    result = await replay_log_file(log_file, "checkwatt", dry_run=False)

                    assert result is False

    @pytest.mark.asyncio
    async def test_replay_exception(self, tmp_path):
        """Test replay when exception occurs during processing."""
        log_file = tmp_path / "test.json"

        with patch("src.tools.replay_json_logs.JSONDataLogger") as mock_logger_class:
            mock_logger = Mock()
            mock_logger.load_log.return_value = {
                "timestamp": "2024-01-15T10:00:00",
                "data": {"test": "data"},
                "metadata": {},
            }
            mock_logger_class.return_value = mock_logger

            with patch("src.data_collection.checkwatt.process_checkwatt_data") as mock_process:
                mock_process.side_effect = Exception("Processing error")

                result = await replay_log_file(log_file, "checkwatt", dry_run=False)

                assert result is False


class TestReplayLogs:
    """Tests for replay_logs function."""

    @pytest.mark.asyncio
    async def test_replay_logs_success(self, tmp_path):
        """Test successful replay of multiple logs."""
        log_file1 = tmp_path / "log1.json"
        log_file2 = tmp_path / "log2.json"

        with patch("src.tools.replay_json_logs.JSONDataLogger") as mock_logger_class:
            mock_logger = Mock()
            mock_logger.get_recent_logs.return_value = [log_file1, log_file2]
            mock_logger_class.return_value = mock_logger

            with patch("src.tools.replay_json_logs.replay_log_file") as mock_replay:
                mock_replay.return_value = True

                success, failure = await replay_logs("test_source", days=7, dry_run=False)

                assert success == 2
                assert failure == 0
                assert mock_replay.call_count == 2

    @pytest.mark.asyncio
    async def test_replay_logs_partial_failure(self, tmp_path):
        """Test replay with some failures."""
        log_file1 = tmp_path / "log1.json"
        log_file2 = tmp_path / "log2.json"
        log_file3 = tmp_path / "log3.json"

        with patch("src.tools.replay_json_logs.JSONDataLogger") as mock_logger_class:
            mock_logger = Mock()
            mock_logger.get_recent_logs.return_value = [log_file1, log_file2, log_file3]
            mock_logger_class.return_value = mock_logger

            with patch("src.tools.replay_json_logs.replay_log_file") as mock_replay:
                mock_replay.side_effect = [True, False, True]

                success, failure = await replay_logs("test_source", days=7, dry_run=False)

                assert success == 2
                assert failure == 1

    @pytest.mark.asyncio
    async def test_replay_logs_no_files(self):
        """Test replay when no log files found."""
        with patch("src.tools.replay_json_logs.JSONDataLogger") as mock_logger_class:
            mock_logger = Mock()
            mock_logger.get_recent_logs.return_value = []
            mock_logger_class.return_value = mock_logger

            success, failure = await replay_logs("test_source", days=7, dry_run=False)

            assert success == 0
            assert failure == 0

    @pytest.mark.asyncio
    async def test_replay_logs_with_limit(self, tmp_path):
        """Test replay with file limit."""
        log_files = [tmp_path / f"log{i}.json" for i in range(10)]

        with patch("src.tools.replay_json_logs.JSONDataLogger") as mock_logger_class:
            mock_logger = Mock()
            mock_logger.get_recent_logs.return_value = log_files
            mock_logger_class.return_value = mock_logger

            with patch("src.tools.replay_json_logs.replay_log_file") as mock_replay:
                mock_replay.return_value = True

                success, failure = await replay_logs("test_source", days=7, dry_run=False, limit=3)

                assert success == 3
                assert failure == 0
                assert mock_replay.call_count == 3

    @pytest.mark.asyncio
    async def test_replay_logs_exception(self, tmp_path):
        """Test replay when exception occurs during processing."""
        log_file1 = tmp_path / "log1.json"
        log_file2 = tmp_path / "log2.json"

        with patch("src.tools.replay_json_logs.JSONDataLogger") as mock_logger_class:
            mock_logger = Mock()
            mock_logger.get_recent_logs.return_value = [log_file1, log_file2]
            mock_logger_class.return_value = mock_logger

            with patch("src.tools.replay_json_logs.replay_log_file") as mock_replay:
                mock_replay.side_effect = [Exception("Error"), True]

                success, failure = await replay_logs("test_source", days=7, dry_run=False)

                assert success == 1
                assert failure == 1


class TestMain:
    """Tests for main entry point."""

    def test_main_list_mode(self, tmp_path, monkeypatch):
        """Test main in list mode."""
        log_dir = tmp_path / "data_logs"
        log_dir.mkdir()

        source_dir = log_dir / "test_source"
        source_dir.mkdir()
        (source_dir / "log1.json").write_text("{}")

        monkeypatch.chdir(tmp_path)

        with patch("sys.argv", ["replay_json_logs.py", "--list"]):
            result = main()

            assert result == 0

    def test_main_list_specific_source(self, tmp_path, monkeypatch):
        """Test main listing specific source."""
        log_dir = tmp_path / "data_logs"
        log_dir.mkdir()

        source_dir = log_dir / "test_source"
        source_dir.mkdir()
        (source_dir / "log1.json").write_text("{}")

        monkeypatch.chdir(tmp_path)

        with patch("sys.argv", ["replay_json_logs.py", "--list", "--source", "test_source"]):
            result = main()

            assert result == 0

    def test_main_list_no_logs(self, tmp_path, monkeypatch):
        """Test main listing when no logs found."""
        monkeypatch.chdir(tmp_path)

        with patch("sys.argv", ["replay_json_logs.py", "--list"]):
            result = main()

            assert result == 0

    def test_main_no_source_arg(self):
        """Test main without source argument."""
        with patch("sys.argv", ["replay_json_logs.py"]):
            result = main()

            assert result == 1

    def test_main_replay_success(self):
        """Test main replay mode with success."""
        with patch("sys.argv", ["replay_json_logs.py", "--source", "test_source"]):
            with patch("src.tools.replay_json_logs.replay_logs"):
                with patch("asyncio.run") as mock_asyncio_run:
                    mock_asyncio_run.return_value = (5, 0)

                    result = main()

                    assert result == 0

    def test_main_replay_with_failures(self):
        """Test main replay mode with some failures."""
        with patch("sys.argv", ["replay_json_logs.py", "--source", "test_source"]):
            with patch("src.tools.replay_json_logs.replay_logs"):
                with patch("asyncio.run") as mock_asyncio_run:
                    mock_asyncio_run.return_value = (3, 2)

                    result = main()

                    assert result == 1

    def test_main_replay_with_options(self):
        """Test main with all options."""
        with patch(
            "sys.argv",
            [
                "replay_json_logs.py",
                "--source",
                "test_source",
                "--days",
                "30",
                "--dry-run",
                "--limit",
                "5",
                "--verbose",
            ],
        ):
            with patch("src.tools.replay_json_logs.replay_logs"):
                with patch("asyncio.run") as mock_asyncio_run:
                    mock_asyncio_run.return_value = (5, 0)

                    result = main()

                    assert result == 0

    def test_main_exception(self):
        """Test main when exception occurs."""
        with patch("sys.argv", ["replay_json_logs.py", "--source", "test_source"]):
            with patch("asyncio.run") as mock_asyncio_run:
                mock_asyncio_run.side_effect = Exception("Unhandled error")

                result = main()

                assert result == 1
