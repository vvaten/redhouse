"""Unit tests for heating program executor."""

import datetime
import json
import os
import sys
import tempfile
from unittest.mock import Mock, patch

import pytest

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.control.program_executor import HeatingProgramExecutor


@pytest.fixture
def mock_config():
    """Create mock configuration."""
    config = Mock()
    config.get = Mock(return_value="load_control")
    return config


@pytest.fixture
def mock_influx_client():
    """Mock InfluxClient."""
    with patch("src.control.program_executor.InfluxClient") as mock:
        mock_instance = Mock()
        mock_instance.write_api = Mock()
        mock_instance.write_api.write = Mock()
        mock.return_value = mock_instance
        yield mock


@pytest.fixture
def mock_load_controller():
    """Mock MultiLoadController."""
    with patch("src.control.program_executor.MultiLoadController") as mock:
        mock_instance = Mock()
        mock_instance.execute_load_command = Mock(
            return_value={
                "success": True,
                "command": "ON",
                "scheduled_time": 1600000000,
                "actual_time": 1600000010,
                "delay_seconds": 10,
            }
        )
        # Mock pump_controller for EVU cycle checks
        mock_pump = Mock()
        mock_pump.check_evu_cycle_needed = Mock(return_value=False)
        mock_pump.perform_evu_cycle = Mock(return_value={"success": True})
        mock_instance.pump_controller = mock_pump

        mock.return_value = mock_instance
        yield mock


@pytest.fixture
def sample_program():
    """Create a sample heating program."""
    return {
        "version": "2.0",
        "program_date": "2024-01-15",
        "generated_at": 1705276800,
        "loads": {
            "pump": {
                "type": "geothermal_pump",
                "schedule": [
                    {
                        "timestamp": 1600000000,
                        "local_time": "2024-01-15 10:00:00",
                        "command": "ON",
                        "power_kw": 2.2,
                        "reason": "cheap_electricity",
                    },
                    {
                        "timestamp": 1600003600,
                        "local_time": "2024-01-15 11:00:00",
                        "command": "OFF",
                        "power_kw": 0.0,
                        "reason": "schedule",
                    },
                ],
            }
        },
    }


class TestHeatingProgramExecutorInit:
    """Tests for HeatingProgramExecutor initialization."""

    def test_init_default(self, mock_config, mock_influx_client, mock_load_controller):
        """Test default initialization (without STAGING_MODE)."""
        # Explicitly unset STAGING_MODE for this test to verify default behavior
        env_patch = {"STAGING_MODE": "false"} if os.getenv("STAGING_MODE") else {}
        with (
            patch("src.control.program_executor.get_config", return_value=mock_config),
            patch.dict(os.environ, env_patch, clear=False),
        ):
            executor = HeatingProgramExecutor()

            assert executor.config == mock_config
            assert executor.dry_run is False
            assert executor.influx is not None
            assert executor.load_controller is not None

    def test_init_with_config(self, mock_config, mock_influx_client, mock_load_controller):
        """Test initialization with provided config."""
        executor = HeatingProgramExecutor(config=mock_config)

        assert executor.config == mock_config

    def test_init_with_dry_run(self, mock_config, mock_influx_client, mock_load_controller):
        """Test initialization with dry-run mode."""
        executor = HeatingProgramExecutor(config=mock_config, dry_run=True)

        assert executor.dry_run is True

    def test_init_with_staging_mode(self, mock_config, mock_influx_client, mock_load_controller):
        """Test initialization with STAGING_MODE env var."""
        with patch.dict(os.environ, {"STAGING_MODE": "true"}):
            executor = HeatingProgramExecutor(config=mock_config)

            assert executor.dry_run is True


class TestLoadProgram:
    """Tests for load_program method."""

    def test_load_program_success(
        self, mock_config, mock_influx_client, mock_load_controller, sample_program
    ):
        """Test loading program successfully."""
        executor = HeatingProgramExecutor(config=mock_config)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create program file
            year_month = "2024-01"
            os.makedirs(os.path.join(tmpdir, year_month))
            program_path = os.path.join(
                tmpdir, year_month, "heating_program_schedule_2024-01-15.json"
            )
            with open(program_path, "w") as f:
                json.dump(sample_program, f)

            # Load it
            loaded = executor.load_program(program_date="2024-01-15", base_dir=tmpdir)

            assert loaded["version"] == "2.0"
            assert loaded["program_date"] == "2024-01-15"
            assert "pump" in loaded["loads"]

    def test_load_program_defaults_to_today(
        self, mock_config, mock_influx_client, mock_load_controller, sample_program
    ):
        """Test load_program defaults to today's date."""
        executor = HeatingProgramExecutor(config=mock_config)

        today = datetime.date.today()
        year_month = today.strftime("%Y-%m")
        date_str = today.strftime("%Y-%m-%d")

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, year_month))
            program_path = os.path.join(
                tmpdir, year_month, f"heating_program_schedule_{date_str}.json"
            )
            sample_program["program_date"] = date_str
            with open(program_path, "w") as f:
                json.dump(sample_program, f)

            loaded = executor.load_program(base_dir=tmpdir)

            assert loaded["program_date"] == date_str

    def test_load_program_file_not_found(
        self, mock_config, mock_influx_client, mock_load_controller
    ):
        """Test load_program raises FileNotFoundError."""
        executor = HeatingProgramExecutor(config=mock_config)

        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(FileNotFoundError):
                executor.load_program(program_date="2024-01-15", base_dir=tmpdir)

    def test_load_program_invalid_no_version(
        self, mock_config, mock_influx_client, mock_load_controller
    ):
        """Test load_program raises ValueError for invalid program (no version)."""
        executor = HeatingProgramExecutor(config=mock_config)

        with tempfile.TemporaryDirectory() as tmpdir:
            year_month = "2024-01"
            os.makedirs(os.path.join(tmpdir, year_month))
            program_path = os.path.join(
                tmpdir, year_month, "heating_program_schedule_2024-01-15.json"
            )
            with open(program_path, "w") as f:
                json.dump({"program_date": "2024-01-15"}, f)  # Missing version

            with pytest.raises(ValueError, match="missing 'version'"):
                executor.load_program(program_date="2024-01-15", base_dir=tmpdir)

    def test_load_program_invalid_no_loads(
        self, mock_config, mock_influx_client, mock_load_controller
    ):
        """Test load_program raises ValueError for invalid program (no loads)."""
        executor = HeatingProgramExecutor(config=mock_config)

        with tempfile.TemporaryDirectory() as tmpdir:
            year_month = "2024-01"
            os.makedirs(os.path.join(tmpdir, year_month))
            program_path = os.path.join(
                tmpdir, year_month, "heating_program_schedule_2024-01-15.json"
            )
            with open(program_path, "w") as f:
                json.dump({"version": "2.0", "program_date": "2024-01-15"}, f)  # Missing loads

            with pytest.raises(ValueError, match="missing 'loads'"):
                executor.load_program(program_date="2024-01-15", base_dir=tmpdir)


class TestSaveProgram:
    """Tests for save_program method."""

    def test_save_program(
        self, mock_config, mock_influx_client, mock_load_controller, sample_program
    ):
        """Test saving program."""
        executor = HeatingProgramExecutor(config=mock_config)

        with tempfile.TemporaryDirectory() as tmpdir:
            year_month = "2024-01"
            os.makedirs(os.path.join(tmpdir, year_month))

            executor.save_program(sample_program, base_dir=tmpdir)

            # Verify file was created
            program_path = os.path.join(
                tmpdir, year_month, "heating_program_schedule_2024-01-15.json"
            )
            assert os.path.exists(program_path)

            # Verify content
            with open(program_path) as f:
                saved = json.load(f)
            assert saved["program_date"] == "2024-01-15"


class TestExecuteProgram:
    """Tests for execute_program method."""

    def test_execute_program_no_commands_to_execute(
        self, mock_config, mock_influx_client, mock_load_controller, sample_program
    ):
        """Test execute_program when no commands are ready."""
        executor = HeatingProgramExecutor(config=mock_config)

        # Set current time before first command
        current_time = 1599999000  # Before first command at 1600000000

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "2024-01"))
            summary = executor.execute_program(
                sample_program, current_time=current_time, base_dir=tmpdir
            )

            assert summary["executed_count"] == 0
            assert summary["skipped_count"] == 0
            assert summary["next_execution_time"] == 1600000000

    def test_execute_program_executes_pending_command(
        self, mock_config, mock_influx_client, mock_load_controller, sample_program
    ):
        """Test execute_program executes pending commands."""
        executor = HeatingProgramExecutor(config=mock_config)

        # Set current time after first command
        current_time = 1600000010  # 10 seconds after first command

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "2024-01"))
            summary = executor.execute_program(
                sample_program, current_time=current_time, base_dir=tmpdir
            )

            assert summary["executed_count"] == 1
            assert summary["skipped_count"] == 0
            assert sample_program["loads"]["pump"]["schedule"][0].get("executed_at") == current_time

    def test_execute_program_skips_delayed_commands(
        self, mock_config, mock_influx_client, mock_load_controller, sample_program
    ):
        """Test execute_program skips commands with excessive delay."""
        executor = HeatingProgramExecutor(config=mock_config)

        # Set current time way past MAX_EXECUTION_DELAY
        current_time = 1600000000 + 3600  # 1 hour after first command

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "2024-01"))
            summary = executor.execute_program(
                sample_program, current_time=current_time, base_dir=tmpdir
            )

            assert summary["executed_count"] == 1  # Second command executed
            assert summary["skipped_count"] == 1  # First command skipped due to delay

    def test_execute_program_skips_already_executed(
        self, mock_config, mock_influx_client, mock_load_controller, sample_program
    ):
        """Test execute_program skips already executed commands."""
        executor = HeatingProgramExecutor(config=mock_config)

        # Mark first command as executed
        sample_program["loads"]["pump"]["schedule"][0]["executed_at"] = 1600000005

        current_time = 1600000010

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "2024-01"))
            summary = executor.execute_program(
                sample_program, current_time=current_time, base_dir=tmpdir
            )

            assert summary["executed_count"] == 0  # Already executed

    def test_execute_program_handles_command_failure(
        self, mock_config, mock_influx_client, mock_load_controller, sample_program
    ):
        """Test execute_program handles command execution failures."""
        executor = HeatingProgramExecutor(config=mock_config)

        # Make load controller return failure
        mock_load_controller.return_value.execute_load_command.return_value = {
            "success": False,
            "error": "Test error",
        }

        current_time = 1600000010

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "2024-01"))
            summary = executor.execute_program(
                sample_program, current_time=current_time, base_dir=tmpdir
            )

            assert summary["executed_count"] == 0
            assert summary["failed_count"] == 1


class TestExecuteCommand:
    """Tests for _execute_command method."""

    def test_execute_command_success(self, mock_config, mock_influx_client, mock_load_controller):
        """Test _execute_command successfully."""
        executor = HeatingProgramExecutor(config=mock_config)

        entry = {
            "timestamp": 1600000000,
            "local_time": "2024-01-15 10:00:00",
            "command": "ON",
        }

        result = executor._execute_command("pump", entry, 1600000000, 1600000010)

        assert result["success"] is True
        assert mock_load_controller.return_value.execute_load_command.called

    def test_execute_command_exception_handling(
        self, mock_config, mock_influx_client, mock_load_controller
    ):
        """Test _execute_command handles exceptions."""
        executor = HeatingProgramExecutor(config=mock_config)

        # Make load controller raise an exception
        mock_load_controller.return_value.execute_load_command.side_effect = Exception(
            "Test exception"
        )

        entry = {
            "timestamp": 1600000000,
            "local_time": "2024-01-15 10:00:00",
            "command": "ON",
        }

        result = executor._execute_command("pump", entry, 1600000000, 1600000010)

        assert result["success"] is False
        assert "Test exception" in result["error"]


class TestWriteExecutionToInflux:
    """Tests for _write_execution_to_influx method."""

    def test_write_execution_to_influx_success(
        self, mock_config, mock_influx_client, mock_load_controller
    ):
        """Test writing execution to InfluxDB (without STAGING_MODE)."""
        # Explicitly unset STAGING_MODE for this test to ensure writes happen
        env_patch = {"STAGING_MODE": "false"} if os.getenv("STAGING_MODE") else {}
        with patch.dict(os.environ, env_patch, clear=False):
            executor = HeatingProgramExecutor(config=mock_config)

            entry = {"command": "ON", "power_kw": 2.2, "reason": "cheap_electricity"}
            result = {
                "success": True,
                "scheduled_time": 1600000000,
                "actual_time": 1600000010,
                "delay_seconds": 10,
            }

            # Point is imported locally in the method, so patch influxdb_client module
            with patch("influxdb_client.Point") as mock_point:
                mock_point_instance = Mock()
                mock_point_instance.tag.return_value = mock_point_instance
                mock_point_instance.field.return_value = mock_point_instance
                mock_point_instance.time.return_value = mock_point_instance
                mock_point.return_value = mock_point_instance

                executor._write_execution_to_influx("2024-01-15", "pump", entry, result)

                assert mock_influx_client.return_value.write_api.write.called

    def test_write_execution_to_influx_dry_run(
        self, mock_config, mock_influx_client, mock_load_controller
    ):
        """Test writing to InfluxDB skipped in dry-run mode."""
        executor = HeatingProgramExecutor(config=mock_config, dry_run=True)

        entry = {"command": "ON"}
        result = {
            "success": True,
            "scheduled_time": 1600000000,
            "actual_time": 1600000010,
            "delay_seconds": 10,
        }

        executor._write_execution_to_influx("2024-01-15", "pump", entry, result)

        assert not mock_influx_client.return_value.write_api.write.called


class TestHandleDayTransition:
    """Tests for handle_day_transition method."""

    def test_handle_day_transition_no_yesterday(
        self, mock_config, mock_influx_client, mock_load_controller, sample_program
    ):
        """Test handle_day_transition with no yesterday program."""
        executor = HeatingProgramExecutor(config=mock_config)

        result = executor.handle_day_transition(sample_program, yesterday_program=None)

        assert result == sample_program

    def test_handle_day_transition_no_unexecuted_commands(
        self, mock_config, mock_influx_client, mock_load_controller, sample_program
    ):
        """Test handle_day_transition when all yesterday commands were executed."""
        executor = HeatingProgramExecutor(config=mock_config)

        yesterday_program = {
            "program_date": "2024-01-14",
            "loads": {
                "pump": {
                    "schedule": [
                        {
                            "timestamp": 1599900000,
                            "local_time": "2024-01-14 10:00:00",
                            "command": "ON",
                            "executed_at": 1599900005,  # Already executed
                        }
                    ]
                }
            },
        }

        result = executor.handle_day_transition(sample_program, yesterday_program)

        # No commands should be merged
        assert len(result["loads"]["pump"]["schedule"]) == 2  # Original 2 commands

    def test_handle_day_transition_merges_unexecuted(
        self, mock_config, mock_influx_client, mock_load_controller, sample_program
    ):
        """Test handle_day_transition merges unexecuted commands."""
        executor = HeatingProgramExecutor(config=mock_config)

        yesterday_program = {
            "program_date": "2024-01-14",
            "loads": {
                "pump": {
                    "schedule": [
                        {
                            "timestamp": 1599900000,
                            "local_time": "2024-01-14 23:00:00",
                            "command": "ON",
                            # No executed_at - unexecuted
                        }
                    ]
                }
            },
        }

        result = executor.handle_day_transition(sample_program, yesterday_program)

        # Should merge the unexecuted command
        assert len(result["loads"]["pump"]["schedule"]) == 3  # Original 2 + 1 merged

    def test_handle_day_transition_skips_duplicate_timestamps(
        self, mock_config, mock_influx_client, mock_load_controller, sample_program
    ):
        """Test handle_day_transition doesn't duplicate same timestamp."""
        executor = HeatingProgramExecutor(config=mock_config)

        yesterday_program = {
            "program_date": "2024-01-14",
            "loads": {
                "pump": {
                    "schedule": [
                        {
                            "timestamp": 1600000000,  # Same as first in today's program
                            "local_time": "2024-01-15 10:00:00",
                            "command": "ON",
                            # No executed_at
                        }
                    ]
                }
            },
        }

        result = executor.handle_day_transition(sample_program, yesterday_program)

        # Should not duplicate
        assert len(result["loads"]["pump"]["schedule"]) == 2  # Original 2, no merge
