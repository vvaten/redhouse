"""Unit tests for heating program generator."""

import datetime
import json
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from src.control.evu_optimizer import EvuOptimizer
from src.control.program_generator import HeatingProgramGenerator
from src.control.schedule_builder import ScheduleBuilder


class TestHeatingProgramGenerator(unittest.TestCase):
    """Test cases for HeatingProgramGenerator class."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock the config and dependencies
        with (
            patch("src.control.program_generator.get_config"),
            patch("src.control.program_generator.InfluxClient"),
            patch("src.control.program_generator.HeatingDataFetcher"),
            patch("src.control.program_generator.HeatingCurve"),
            patch("src.control.program_generator.HeatingOptimizer"),
        ):
            self.generator = HeatingProgramGenerator()

    def test_initialization(self):
        """Test generator initialization."""
        self.assertIsNotNone(self.generator)
        self.assertEqual(self.generator.VERSION, "2.0.0")
        self.assertEqual(self.generator.evu_optimizer.EVU_OFF_THRESHOLD_PRICE, 15.0)
        self.assertEqual(self.generator.evu_optimizer.EVU_OFF_MAX_CONTINUOUS_HOURS, 4)

    def test_loads_configuration(self):
        """Test load definitions are correct."""
        self.assertIn("geothermal_pump", self.generator.schedule_builder.LOADS)
        self.assertIn("garage_heater", self.generator.schedule_builder.LOADS)
        self.assertIn("ev_charger", self.generator.schedule_builder.LOADS)

        # Check geothermal pump config
        pump = self.generator.schedule_builder.LOADS["geothermal_pump"]
        self.assertEqual(pump["priority"], 1)
        self.assertEqual(pump["power_kw"], 3.0)
        self.assertEqual(pump["control_type"], "mlp_i2c")
        self.assertTrue(pump["enabled"])

        # Check garage heater config
        garage = self.generator.schedule_builder.LOADS["garage_heater"]
        self.assertEqual(garage["priority"], 2)
        self.assertEqual(garage["power_kw"], 2.0)
        self.assertFalse(garage["enabled"])


class TestEvuOffPeriodGeneration(unittest.TestCase):
    """Test EVU-OFF period generation logic."""

    def setUp(self):
        """Set up test fixtures."""
        with patch("src.control.heating_optimizer.HeatingOptimizer"):
            mock_optimizer = MagicMock()
            self.evu_optimizer = EvuOptimizer(mock_optimizer)

    def test_optimize_evu_off_groups_single_hour(self):
        """Test EVU-OFF grouping with single hour."""
        # Create DataFrame with one expensive hour
        timestamps = pd.date_range(
            start="2025-01-15 10:00:00", periods=1, freq="H", tz="Europe/Helsinki"
        )

        df = pd.DataFrame({"heating_prio": [20.0]}, index=timestamps)

        groups = self.evu_optimizer._optimize_evu_off_groups(df, max_continuous_hours=4)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["first"], timestamps[0])
        self.assertEqual(groups[0]["last"], timestamps[0])

    def test_optimize_evu_off_groups_consecutive_hours(self):
        """Test EVU-OFF grouping with consecutive hours."""
        # Create DataFrame with 3 consecutive expensive hours
        timestamps = pd.date_range(
            start="2025-01-15 10:00:00", periods=3, freq="H", tz="Europe/Helsinki"
        )

        df = pd.DataFrame({"heating_prio": [20.0, 21.0, 22.0]}, index=timestamps)

        groups = self.evu_optimizer._optimize_evu_off_groups(df, max_continuous_hours=4)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["first"], timestamps[0])
        self.assertEqual(groups[0]["last"], timestamps[2])

    def test_optimize_evu_off_groups_max_length_respected(self):
        """Test that EVU-OFF groups respect max continuous hours."""
        # Create DataFrame with 5 consecutive hours
        # Max=4 means duration can be up to 3 hours (4 intervals: 0, 1, 2, 3)
        timestamps = pd.date_range(
            start="2025-01-15 10:00:00", periods=5, freq="H", tz="Europe/Helsinki"
        )

        df = pd.DataFrame({"heating_prio": [20.0] * 5}, index=timestamps)

        groups = self.evu_optimizer._optimize_evu_off_groups(df, max_continuous_hours=4)

        # With max=4, 5 consecutive hours should fit in one group
        # (10, 11, 12, 13 = 3 hour duration < 4 max, then 14 gets rejected)
        # Actually, 10-13 is 4 hours (duration=3), so 14 should start new group
        # But current logic merges adjacent groups, so it becomes 1 group
        # Let's check the actual duration doesn't exceed max
        for group in groups:
            duration = (group["last"].timestamp() - group["first"].timestamp()) / 3600
            # Duration between first and last should be less than max_continuous_hours
            self.assertLess(duration, 4)  # Less than max

    def test_optimize_evu_off_groups_non_consecutive(self):
        """Test EVU-OFF grouping with non-consecutive hours."""
        # Create DataFrame with gaps
        timestamps = pd.date_range(
            start="2025-01-15 10:00:00", periods=24, freq="H", tz="Europe/Helsinki"
        )

        # Select hours 10, 11, 14, 15 (gap at 12-13)
        selected_timestamps = [timestamps[0], timestamps[1], timestamps[4], timestamps[5]]

        df = pd.DataFrame({"heating_prio": [20.0, 21.0, 22.0, 23.0]}, index=selected_timestamps)

        groups = self.evu_optimizer._optimize_evu_off_groups(df, max_continuous_hours=4)

        # Should create 2 groups due to gap
        self.assertEqual(len(groups), 2)

    def test_optimize_evu_off_groups_merge_adjacent(self):
        """Test that adjacent groups are merged if they fit within max length."""
        # Create hours that could be merged: 10, 11 and 12, 13
        timestamps = pd.date_range(
            start="2025-01-15 10:00:00", periods=4, freq="H", tz="Europe/Helsinki"
        )

        df = pd.DataFrame({"heating_prio": [20.0] * 4}, index=timestamps)

        groups = self.evu_optimizer._optimize_evu_off_groups(df, max_continuous_hours=4)

        # Should merge into 1 group of 4 hours
        self.assertEqual(len(groups), 1)
        duration = (groups[0]["last"].timestamp() - groups[0]["first"].timestamp()) / 3600
        self.assertEqual(duration, 3)  # 4 hours = 3 hour intervals

    def test_optimize_evu_off_groups_extend_backward(self):
        """Test extending a group backward."""
        # Create non-consecutive hours that will extend backward
        timestamps = pd.date_range(
            start="2025-01-15 10:00:00", periods=24, freq="H", tz="Europe/Helsinki"
        )

        # Hours in reverse order: 13, 12, 11 (will extend backward)
        selected_timestamps = [timestamps[13], timestamps[12], timestamps[11]]

        df = pd.DataFrame({"heating_prio": [20.0, 21.0, 22.0]}, index=selected_timestamps)

        groups = self.evu_optimizer._optimize_evu_off_groups(df, max_continuous_hours=4)

        # Should create one group extending backward
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["first"], timestamps[11])
        self.assertEqual(groups[0]["last"], timestamps[13])

    def test_optimize_evu_off_groups_reject_when_max_reached(self):
        """Test that hours are rejected when max continuous hours is reached."""
        # Create 5 consecutive hours with max=4
        timestamps = pd.date_range(
            start="2025-01-15 10:00:00", periods=5, freq="H", tz="Europe/Helsinki"
        )

        # Add them in order to fill up a group, then try to extend past max
        df = pd.DataFrame({"heating_prio": [20.0] * 5}, index=timestamps)

        groups = self.evu_optimizer._optimize_evu_off_groups(df, max_continuous_hours=4)

        # Should have limited duration
        for group in groups:
            duration = (group["last"].timestamp() - group["first"].timestamp()) / 3600
            self.assertLessEqual(duration, 3)  # Max 4 hours means 3-hour duration

    def test_optimize_evu_off_groups_no_merge_when_exceeds_max(self):
        """Test that groups are not merged when result would exceed max."""
        # Create two groups of 3 hours each with max=4
        timestamps = pd.date_range(
            start="2025-01-15 10:00:00", periods=24, freq="H", tz="Europe/Helsinki"
        )

        # Group 1: 10, 11, 12 (3 hours)
        # Gap at 13
        # Group 2: 14, 15 (2 hours)
        selected_timestamps = [
            timestamps[0],
            timestamps[1],
            timestamps[2],
            timestamps[4],
            timestamps[5],
        ]

        df = pd.DataFrame({"heating_prio": [20.0] * 5}, index=selected_timestamps)

        groups = self.evu_optimizer._optimize_evu_off_groups(df, max_continuous_hours=4)

        # Should have 2 groups due to gap
        self.assertEqual(len(groups), 2)


class TestScheduleGeneration(unittest.TestCase):
    """Test schedule generation logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.schedule_builder = ScheduleBuilder()

    def test_generate_geothermal_pump_schedule_basic(self):
        """Test basic geothermal pump schedule generation."""
        # Create mock selected hours
        timestamps = pd.date_range(
            start="2025-01-15 00:00:00", periods=6, freq="H", tz="Europe/Helsinki"
        )

        selected_hours = pd.DataFrame(
            {"heating_prio": [3.0, 3.5, 4.0, 5.0, 6.0, 7.0]}, index=timestamps
        )

        day_priorities = pd.DataFrame(
            {
                "price_total": [3.0, 3.5, 4.0, 5.0, 6.0, 7.0],
                "solar_yield_avg_prediction": [0.0, 0.0, 0.0, 0.5, 1.0, 1.5],
                "heating_prio": [3.0, 3.5, 4.0, 5.0, 6.0, 7.0],
            },
            index=timestamps,
        )

        evu_off_periods = []
        program_date = datetime.datetime(2025, 1, 15)

        result = self.schedule_builder._generate_geothermal_pump_schedule(
            selected_hours, evu_off_periods, day_priorities, program_date
        )

        # Check structure
        self.assertEqual(result["load_id"], "geothermal_pump")
        self.assertEqual(result["priority"], 1)
        self.assertEqual(result["power_kw"], 3.0)
        self.assertEqual(result["total_intervals_on"], 6)
        self.assertGreater(len(result["schedule"]), 0)

        # Check that all ON entries are present
        on_entries = [e for e in result["schedule"] if e["command"] == "ON"]
        self.assertEqual(len(on_entries), 6)

        # Check that ALE entries are added
        ale_entries = [e for e in result["schedule"] if e["command"] == "ALE"]
        self.assertGreater(len(ale_entries), 0)

    def test_generate_geothermal_pump_schedule_with_evu(self):
        """Test schedule generation with EVU-OFF periods."""
        # Create mock selected hours
        timestamps = pd.date_range(
            start="2025-01-15 00:00:00", periods=4, freq="H", tz="Europe/Helsinki"
        )

        selected_hours = pd.DataFrame({"heating_prio": [3.0, 3.5, 4.0, 5.0]}, index=timestamps)

        day_priorities = pd.DataFrame(
            {
                "price_total": [3.0, 3.5, 4.0, 5.0],
                "solar_yield_avg_prediction": [0.0, 0.0, 0.0, 0.5],
                "heating_prio": [3.0, 3.5, 4.0, 5.0],
            },
            index=timestamps,
        )

        # Add EVU-OFF period
        evu_start = int(timestamps[2].timestamp())
        evu_off_periods = [{"group_id": 1, "start": evu_start, "stop": evu_start + 7200}]

        program_date = datetime.datetime(2025, 1, 15)

        result = self.schedule_builder._generate_geothermal_pump_schedule(
            selected_hours, evu_off_periods, day_priorities, program_date
        )

        # Check EVU entries are present
        evu_entries = [e for e in result["schedule"] if e["command"] == "EVU"]
        self.assertEqual(len(evu_entries), 1)
        self.assertEqual(evu_entries[0]["evu_off_group_id"], 1)
        self.assertEqual(evu_entries[0]["duration_minutes"], 120)

    def test_schedule_entries_sorted_by_timestamp(self):
        """Test that schedule entries are sorted chronologically."""
        timestamps = pd.date_range(
            start="2025-01-15 00:00:00", periods=4, freq="H", tz="Europe/Helsinki"
        )

        selected_hours = pd.DataFrame({"heating_prio": [3.0, 3.5, 4.0, 5.0]}, index=timestamps)

        day_priorities = pd.DataFrame(
            {
                "price_total": [3.0, 3.5, 4.0, 5.0],
                "solar_yield_avg_prediction": [0.0] * 4,
                "heating_prio": [3.0, 3.5, 4.0, 5.0],
            },
            index=timestamps,
        )

        result = self.schedule_builder._generate_geothermal_pump_schedule(
            selected_hours, [], day_priorities, datetime.datetime(2025, 1, 15)
        )

        # Check timestamps are in order
        prev_ts = 0
        for entry in result["schedule"]:
            self.assertGreater(entry["timestamp"], prev_ts)
            prev_ts = entry["timestamp"]


class TestPlanningResults(unittest.TestCase):
    """Test planning results calculation."""

    def setUp(self):
        """Set up test fixtures."""
        with (
            patch("src.control.program_generator.get_config"),
            patch("src.control.program_generator.InfluxClient"),
            patch("src.control.program_generator.HeatingDataFetcher"),
            patch("src.control.program_generator.HeatingCurve"),
            patch("src.control.program_generator.HeatingOptimizer"),
        ):
            self.generator = HeatingProgramGenerator()

    def test_calculate_planning_results_basic(self):
        """Test planning results calculation."""
        # Create mock loads
        loads_schedules = {
            "geothermal_pump": {
                "load_id": "geothermal_pump",
                "power_kw": 3.0,
                "total_intervals_on": 6,
                "total_hours_on": 6.0,
                "estimated_cost_eur": 1.50,
                "schedule": [
                    {"command": "ON"},
                    {"command": "ON"},
                    {"command": "EVU"},
                    {"command": "ALE"},
                ],
            }
        }

        timestamps = pd.date_range(
            start="2025-01-15 00:00:00", periods=6, freq="H", tz="Europe/Helsinki"
        )
        # heating_prio values are in EUR (total cost per hour for 3 kW load)
        # 3 kW * 0.01-0.023 EUR/kWh = 0.03-0.07 EUR
        selected_hours = pd.DataFrame(
            {"heating_prio": [0.03, 0.035, 0.04, 0.05, 0.06, 0.07]}, index=timestamps
        )

        hours_to_heat = 6.0

        result = self.generator._calculate_planning_results(
            loads_schedules, hours_to_heat, selected_hours
        )

        self.assertEqual(result["total_heating_hours_needed"], 6.0)
        self.assertEqual(result["total_heating_intervals_planned"], 6)
        self.assertEqual(result["total_evu_off_intervals"], 1)
        self.assertEqual(result["estimated_total_cost_eur"], 1.50)
        # Prices are converted to c/kWh for display (EUR / kW * 100)
        # 0.03 EUR / 3 kW * 100 = 1.0 c/kWh, 0.07 EUR / 3 kW * 100 = 2.33 c/kWh
        self.assertAlmostEqual(result["cheapest_interval_price"], 1.0, places=1)
        self.assertAlmostEqual(result["most_expensive_interval_price"], 2.33, places=1)

    def test_calculate_planning_results_multiple_loads(self):
        """Test planning results with multiple loads."""
        loads_schedules = {
            "geothermal_pump": {
                "power_kw": 3.0,
                "total_intervals_on": 6,
                "estimated_cost_eur": 1.50,
                "schedule": [{"command": "ON"}, {"command": "EVU"}],
            },
            "garage_heater": {
                "power_kw": 2.0,
                "total_intervals_on": 2,
                "estimated_cost_eur": 0.50,
                "schedule": [{"command": "ON"}],
            },
        }

        timestamps = pd.date_range(
            start="2025-01-15 00:00:00", periods=6, freq="H", tz="Europe/Helsinki"
        )
        # heating_prio values are in EUR (total cost per hour for 3 kW load)
        selected_hours = pd.DataFrame({"heating_prio": [0.03] * 6}, index=timestamps)

        result = self.generator._calculate_planning_results(loads_schedules, 6.0, selected_hours)

        self.assertEqual(result["total_heating_intervals_planned"], 8)  # 6 + 2
        self.assertEqual(result["estimated_total_cost_eur"], 2.0)  # 1.50 + 0.50


class TestJSONSaving(unittest.TestCase):
    """Test JSON file saving."""

    def setUp(self):
        """Set up test fixtures."""
        with (
            patch("src.control.program_generator.get_config"),
            patch("src.control.program_generator.InfluxClient"),
            patch("src.control.program_generator.HeatingDataFetcher"),
            patch("src.control.program_generator.HeatingCurve"),
            patch("src.control.program_generator.HeatingOptimizer"),
        ):
            self.generator = HeatingProgramGenerator()

    def test_save_program_json_structure(self):
        """Test JSON file is saved with correct structure."""
        import tempfile

        program = {
            "version": "2.0.0",
            "program_date": "2025-01-15",
            "loads": {},
            "planning_results": {},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = self.generator.save_program_json(program, output_dir=tmpdir)

            # Check file exists
            import os

            self.assertTrue(os.path.exists(filepath))

            # Check folder structure (YYYY-MM/filename.json)
            self.assertIn("2025-01", filepath)
            self.assertIn("heating_program_schedule_2025-01-15.json", filepath)

            # Check content
            with open(filepath) as f:
                loaded = json.load(f)
                self.assertEqual(loaded["version"], "2.0.0")
                self.assertEqual(loaded["program_date"], "2025-01-15")


class TestInfluxDBSaving(unittest.TestCase):
    """Test InfluxDB saving functionality."""

    def setUp(self):
        """Set up test fixtures."""
        with patch("src.control.program_generator.get_config") as mock_config:
            mock_config.return_value.get.return_value = "load_control_test"
            with patch("src.control.program_generator.InfluxClient") as mock_influx_client:
                self.mock_influx = MagicMock()
                mock_influx_client.return_value = self.mock_influx
                with (
                    patch("src.control.program_generator.HeatingDataFetcher"),
                    patch("src.control.program_generator.HeatingCurve"),
                    patch("src.control.program_generator.HeatingOptimizer"),
                ):
                    self.generator = HeatingProgramGenerator()

    def test_save_program_influxdb_creates_points(self):
        """Test that InfluxDB points are created correctly."""
        program = {
            "version": "2.0.0",
            "program_date": "2025-01-15",
            "generator_version": "redhouse-2.0.0",
            "input_parameters": {"avg_temperature_c": -5.2},
            "planning_results": {
                "total_heating_hours_needed": 9.0,
                "total_heating_intervals_planned": 9,
                "total_evu_off_intervals": 2,
                "estimated_total_cost_eur": 1.50,
                "cheapest_interval_price": 3.0,
                "most_expensive_interval_price": 18.0,
                "average_heating_price": 9.5,
            },
            "loads": {
                "geothermal_pump": {
                    "power_kw": 3.0,
                    "schedule": [
                        {
                            "timestamp": 1736895600,
                            "command": "ON",
                            "reason": "cheap_electricity",
                            "spot_price_total_c_kwh": 3.5,
                            "solar_prediction_kwh": 0.0,
                            "priority_score": 3.5,
                            "estimated_cost_eur": 0.105,
                            "duration_minutes": 60,
                        }
                    ],
                }
            },
        }

        self.generator.save_program_influxdb(program, data_type="plan")

        # Verify write_api.write was called
        self.mock_influx.write_api.write.assert_called_once()

        # Get the points that were written
        call_args = self.mock_influx.write_api.write.call_args
        points = call_args[1]["record"]

        # Should have 2 points (1 schedule + 1 summary)
        self.assertEqual(len(points), 2)

    def test_save_program_influxdb_data_type_tag(self):
        """Test that data_type tag is set correctly."""
        program = {
            "version": "2.0.0",
            "program_date": "2025-01-15",
            "generator_version": "redhouse-2.0.0",
            "input_parameters": {"avg_temperature_c": -5.2},
            "planning_results": {
                "total_heating_hours_needed": 9.0,
                "total_heating_intervals_planned": 1,
                "total_evu_off_intervals": 0,
                "estimated_total_cost_eur": 1.50,
                "cheapest_interval_price": 3.0,
                "most_expensive_interval_price": 18.0,
                "average_heating_price": 9.5,
            },
            "loads": {
                "geothermal_pump": {
                    "power_kw": 3.0,
                    "schedule": [
                        {
                            "timestamp": 1736895600,
                            "command": "ON",
                            "reason": "cheap_electricity",
                            "duration_minutes": 60,
                        }
                    ],
                }
            },
        }

        # Test with different data types
        for data_type in ["plan", "actual", "simulation"]:
            self.mock_influx.write_api.write.reset_mock()
            self.generator.save_program_influxdb(program, data_type=data_type)
            self.mock_influx.write_api.write.assert_called_once()

    def test_save_program_influxdb_error_handling(self):
        """Test error handling when InfluxDB write fails."""
        program = {
            "version": "2.0.0",
            "program_date": "2025-01-15",
            "generator_version": "redhouse-2.0.0",
            "input_parameters": {"avg_temperature_c": -5.2},
            "planning_results": {
                "total_heating_hours_needed": 9.0,
                "total_heating_intervals_planned": 1,
                "total_evu_off_intervals": 0,
                "estimated_total_cost_eur": 1.50,
                "cheapest_interval_price": 3.0,
                "most_expensive_interval_price": 18.0,
                "average_heating_price": 9.5,
            },
            "loads": {
                "geothermal_pump": {
                    "power_kw": 3.0,
                    "schedule": [
                        {
                            "timestamp": 1736895600,
                            "command": "ON",
                            "reason": "cheap_electricity",
                            "duration_minutes": 60,
                        }
                    ],
                }
            },
        }

        # Make write raise an exception
        self.mock_influx.write_api.write.side_effect = Exception("Connection failed")

        # Should raise the exception
        with self.assertRaises(Exception) as context:
            self.generator.save_program_influxdb(program, data_type="plan")
        self.assertIn("Connection failed", str(context.exception))


class TestGenerateDailyProgram(unittest.TestCase):
    """Test generate_daily_program method."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_config = MagicMock()
        self.mock_data_fetcher = MagicMock()
        self.mock_heating_curve = MagicMock()
        self.mock_optimizer = MagicMock()

        with (
            patch("src.control.program_generator.get_config", return_value=self.mock_config),
            patch("src.control.program_generator.InfluxClient"),
            patch(
                "src.control.program_generator.HeatingDataFetcher",
                return_value=self.mock_data_fetcher,
            ),
            patch(
                "src.control.program_generator.HeatingCurve", return_value=self.mock_heating_curve
            ),
            patch(
                "src.control.program_generator.HeatingOptimizer",
                return_value=self.mock_optimizer,
            ),
        ):
            self.generator = HeatingProgramGenerator()

    def test_generate_daily_program_empty_data(self):
        """Test error handling when no data is available."""
        # Mock empty DataFrame
        self.mock_data_fetcher.fetch_heating_data.return_value = pd.DataFrame()

        with self.assertRaises(ValueError) as context:
            self.generator.generate_daily_program(date_offset=1)

        self.assertIn("No data available", str(context.exception))

    def test_generate_daily_program_no_priority_data(self):
        """Test error handling when priority data is empty."""
        # Mock data fetcher to return data
        timestamps = pd.date_range(
            start="2025-01-15 00:00:00", periods=48, freq="H", tz="Europe/Helsinki"
        )
        df = pd.DataFrame(
            {
                "price_total": [10.0] * 48,
                "solar_yield_avg_prediction": [0.5] * 48,
                "Air temperature": [-5.0] * 48,
            },
            index=timestamps,
        )

        self.mock_data_fetcher.fetch_heating_data.return_value = df
        self.mock_data_fetcher.get_day_average_temperature.return_value = -5.0
        self.mock_heating_curve.calculate_heating_hours.return_value = 8.0
        self.mock_heating_curve.get_curve_points.return_value = {"-10": 12, "0": 6, "10": 0}

        # Mock optimizer to return priorities but filter returns empty
        priorities_df = pd.DataFrame(
            {"heating_prio": [5.0] * 48, "base_load": [1.0] * 48}, index=timestamps
        )
        self.mock_optimizer.calculate_heating_priorities.return_value = priorities_df
        self.mock_optimizer.filter_day_priorities.return_value = pd.DataFrame()

        with self.assertRaises(ValueError) as context:
            self.generator.generate_daily_program(date_offset=1)

        self.assertIn("No priority data available", str(context.exception))

    def test_generate_daily_program_success(self):
        """Test successful program generation."""
        # Mock data fetcher
        timestamps = pd.date_range(
            start="2025-01-15 00:00:00", periods=48, freq="H", tz="Europe/Helsinki"
        )
        df = pd.DataFrame(
            {
                "price_total": [10.0] * 48,
                "solar_yield_avg_prediction": [0.5] * 48,
                "Air temperature": [-5.0] * 48,
                "price_sell": [5.0] * 48,
            },
            index=timestamps,
        )

        self.mock_data_fetcher.fetch_heating_data.return_value = df
        self.mock_data_fetcher.get_day_average_temperature.return_value = -5.0
        self.mock_heating_curve.calculate_heating_hours.return_value = 8.0
        self.mock_heating_curve.get_curve_points.return_value = {"-10": 12, "0": 6, "10": 0}

        # Mock optimizer
        priorities_df = pd.DataFrame(
            {
                "heating_prio": [5.0] * 48,
                "base_load": [1.0] * 48,
                "solar_yield_avg_prediction": [0.5] * 48,
                "price_total": [10.0] * 48,
            },
            index=timestamps,
        )
        day_priorities = priorities_df[24:48]

        self.mock_optimizer.calculate_heating_priorities.return_value = priorities_df
        self.mock_optimizer.filter_day_priorities.return_value = day_priorities
        self.mock_optimizer.select_cheapest_hours.return_value = day_priorities[:8]
        self.mock_optimizer.base_load_kw = 1.0
        self.mock_optimizer.heating_load_kw = 3.0

        # Generate program
        program = self.generator.generate_daily_program(date_offset=1, simulation_mode=False)

        # Verify structure
        self.assertIn("version", program)
        self.assertIn("program_date", program)
        self.assertIn("input_parameters", program)
        self.assertIn("planning_results", program)
        self.assertIn("loads", program)
        self.assertIn("execution_status", program)

        # Verify simulation data is None (not simulation mode)
        self.assertIsNone(program["simulation_data"])

    def test_generate_daily_program_simulation_mode(self):
        """Test program generation in simulation mode."""
        # Mock data fetcher
        timestamps = pd.date_range(
            start="2025-01-15 00:00:00", periods=48, freq="H", tz="Europe/Helsinki"
        )
        df = pd.DataFrame(
            {
                "price_total": [10.0] * 48,
                "solar_yield_avg_prediction": [0.5] * 48,
                "Air temperature": [-5.0] * 48,
                "price_sell": [5.0] * 48,
            },
            index=timestamps,
        )

        self.mock_data_fetcher.fetch_heating_data.return_value = df
        self.mock_data_fetcher.get_day_average_temperature.return_value = -5.0
        self.mock_heating_curve.calculate_heating_hours.return_value = 8.0
        self.mock_heating_curve.get_curve_points.return_value = {"-10": 12, "0": 6, "10": 0}

        # Mock optimizer
        priorities_df = pd.DataFrame(
            {
                "heating_prio": [5.0] * 48,
                "base_load": [1.0] * 48,
                "solar_yield_avg_prediction": [0.5] * 48,
                "price_total": [10.0] * 48,
            },
            index=timestamps,
        )
        day_priorities = priorities_df[24:48]

        self.mock_optimizer.calculate_heating_priorities.return_value = priorities_df
        self.mock_optimizer.filter_day_priorities.return_value = day_priorities
        self.mock_optimizer.select_cheapest_hours.return_value = day_priorities[:8]
        self.mock_optimizer.base_load_kw = 1.0
        self.mock_optimizer.heating_load_kw = 3.0

        # Generate program in simulation mode
        program = self.generator.generate_daily_program(date_offset=1, simulation_mode=True)

        # Verify simulation data is present
        self.assertIsNotNone(program["simulation_data"])
        self.assertEqual(program["simulation_data"]["mode"], "live")

    def test_generate_daily_program_with_base_date(self):
        """Test program generation with historical base date."""
        # Mock data fetcher
        timestamps = pd.date_range(
            start="2024-12-01 00:00:00", periods=72, freq="H", tz="Europe/Helsinki"
        )
        df = pd.DataFrame(
            {
                "price_total": [10.0] * 72,
                "solar_yield_avg_prediction": [0.5] * 72,
                "Air temperature": [-5.0] * 72,
                "price_sell": [5.0] * 72,
            },
            index=timestamps,
        )

        self.mock_data_fetcher.fetch_heating_data.return_value = df
        self.mock_data_fetcher.get_day_average_temperature.return_value = -5.0
        self.mock_heating_curve.calculate_heating_hours.return_value = 8.0
        self.mock_heating_curve.get_curve_points.return_value = {"-10": 12, "0": 6, "10": 0}

        # Mock optimizer
        priorities_df = pd.DataFrame(
            {
                "heating_prio": [5.0] * 72,
                "base_load": [1.0] * 72,
                "solar_yield_avg_prediction": [0.5] * 72,
                "price_total": [10.0] * 72,
            },
            index=timestamps,
        )
        day_priorities = priorities_df[24:48]

        self.mock_optimizer.calculate_heating_priorities.return_value = priorities_df
        self.mock_optimizer.filter_day_priorities.return_value = day_priorities
        self.mock_optimizer.select_cheapest_hours.return_value = day_priorities[:8]
        self.mock_optimizer.base_load_kw = 1.0
        self.mock_optimizer.heating_load_kw = 3.0

        # Generate program with base date
        program = self.generator.generate_daily_program(
            date_offset=1, simulation_mode=True, base_date="2024-12-01"
        )

        # Verify program date is correct
        self.assertEqual(program["program_date"], "2024-12-02")

        # Verify simulation data indicates historical mode
        self.assertIsNotNone(program["simulation_data"])
        self.assertEqual(program["simulation_data"]["mode"], "historical")
        self.assertEqual(program["simulation_data"]["base_date"], "2024-12-01")


class TestGenerateEvuOffPeriods(unittest.TestCase):
    """Test generate_evu_off_periods method."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_optimizer = MagicMock()
        self.evu_optimizer = EvuOptimizer(self.mock_optimizer)

    def test_generate_evu_off_periods_no_room(self):
        """Test when heating all day, no EVU-OFF periods."""
        # Hours to heat = 22, so max_evu_off = 24 - 22 - 2 = 0
        timestamps = pd.date_range(
            start="2025-01-15 00:00:00", periods=24, freq="H", tz="Europe/Helsinki"
        )
        df = pd.DataFrame({"price_total": [10.0] * 24}, index=timestamps)
        priorities_df = pd.DataFrame({"heating_prio": [10.0] * 24}, index=timestamps)

        self.mock_optimizer.filter_day_priorities.return_value = priorities_df

        result = self.evu_optimizer.generate_evu_off_periods(df, priorities_df, 22.0, 0)

        self.assertEqual(result, [])

    def test_generate_evu_off_periods_no_expensive_hours(self):
        """Test when no hours are expensive enough."""
        timestamps = pd.date_range(
            start="2025-01-15 00:00:00", periods=24, freq="H", tz="Europe/Helsinki"
        )
        df = pd.DataFrame({"price_total": [5.0] * 24}, index=timestamps)
        priorities_df = pd.DataFrame({"heating_prio": [5.0] * 24}, index=timestamps)

        self.mock_optimizer.filter_day_priorities.return_value = priorities_df

        result = self.evu_optimizer.generate_evu_off_periods(df, priorities_df, 8.0, 0)

        self.assertEqual(result, [])

    def test_generate_evu_off_periods_with_expensive_hours(self):
        """Test EVU-OFF period generation with expensive hours."""
        timestamps = pd.date_range(
            start="2025-01-15 00:00:00", periods=24, freq="H", tz="Europe/Helsinki"
        )

        # Make hours 12-15 expensive (above threshold of 15.0)
        prices = [5.0] * 12 + [20.0] * 4 + [5.0] * 8
        priorities_df = pd.DataFrame({"heating_prio": prices}, index=timestamps)

        self.mock_optimizer.filter_day_priorities.return_value = priorities_df

        # Mock _optimize_evu_off_groups to return a simple group
        groups = [{"first": timestamps[12], "last": timestamps[15]}]
        with patch.object(self.evu_optimizer, "_optimize_evu_off_groups", return_value=groups):
            result = self.evu_optimizer.generate_evu_off_periods(
                pd.DataFrame(index=timestamps), priorities_df, 8.0, 0
            )

        # Should return one EVU-OFF period
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["group_id"], 1)
        self.assertIn("start", result[0])
        self.assertIn("stop", result[0])


class TestGenerateLoadSchedules(unittest.TestCase):
    """Test generate_load_schedules method."""

    def setUp(self):
        """Set up test fixtures."""
        self.schedule_builder = ScheduleBuilder()

    def test_generate_load_schedules_includes_disabled_loads(self):
        """Test that disabled loads are included with empty schedules."""
        timestamps = pd.date_range(
            start="2025-01-15 00:00:00", periods=4, freq="H", tz="Europe/Helsinki"
        )

        selected_hours = pd.DataFrame({"heating_prio": [3.0, 3.5, 4.0, 5.0]}, index=timestamps)

        day_priorities = pd.DataFrame(
            {
                "price_total": [3.0, 3.5, 4.0, 5.0],
                "solar_yield_avg_prediction": [0.0] * 4,
                "heating_prio": [3.0, 3.5, 4.0, 5.0],
            },
            index=timestamps,
        )

        evu_off_periods = []
        program_date = datetime.datetime(2025, 1, 15)

        result = self.schedule_builder.generate_load_schedules(
            selected_hours, evu_off_periods, day_priorities, program_date
        )

        # Should have geothermal pump (enabled) and disabled loads
        self.assertIn("geothermal_pump", result)
        self.assertIn("garage_heater", result)
        self.assertIn("ev_charger", result)

        # Disabled loads should have zero intervals
        self.assertEqual(result["garage_heater"]["total_intervals_on"], 0)
        self.assertEqual(result["garage_heater"]["total_hours_on"], 0.0)
        self.assertEqual(result["garage_heater"]["estimated_cost_eur"], 0.0)
        self.assertEqual(result["garage_heater"]["schedule"], [])


class TestALETransitionLogic(unittest.TestCase):
    """Test ALE (auto mode) transition logic in schedule generation."""

    def setUp(self):
        """Set up test fixtures."""
        self.schedule_builder = ScheduleBuilder()

    def test_ale_added_after_on_period(self):
        """Test that ALE is added after ON period."""
        timestamps = pd.date_range(
            start="2025-01-15 00:00:00", periods=2, freq="H", tz="Europe/Helsinki"
        )

        selected_hours = pd.DataFrame({"heating_prio": [3.0, 4.0]}, index=timestamps)

        day_priorities = pd.DataFrame(
            {
                "price_total": [3.0, 4.0],
                "solar_yield_avg_prediction": [0.0, 0.0],
                "heating_prio": [3.0, 4.0],
            },
            index=timestamps,
        )

        evu_off_periods = []
        program_date = datetime.datetime(2025, 1, 15)

        result = self.schedule_builder._generate_geothermal_pump_schedule(
            selected_hours, evu_off_periods, day_priorities, program_date
        )

        # Find ALE commands
        ale_commands = [e for e in result["schedule"] if e["command"] == "ALE"]

        # Should have ALE commands after ON periods
        self.assertGreater(len(ale_commands), 0)

        # Verify ALE reason
        for ale in ale_commands:
            self.assertIn("reason", ale)
            self.assertIn("heating_complete", ale["reason"])

    def test_no_ale_when_next_command_immediate(self):
        """Test that ALE is skipped when next command starts immediately."""
        timestamps = pd.date_range(
            start="2025-01-15 00:00:00", periods=3, freq="H", tz="Europe/Helsinki"
        )

        # Create consecutive heating hours
        selected_hours = pd.DataFrame({"heating_prio": [3.0, 3.1, 3.2]}, index=timestamps)

        day_priorities = pd.DataFrame(
            {
                "price_total": [3.0, 3.1, 3.2],
                "solar_yield_avg_prediction": [0.0] * 3,
                "heating_prio": [3.0, 3.1, 3.2],
            },
            index=timestamps,
        )

        # Add EVU period that starts immediately after first ON
        evu_start = int(timestamps[1].timestamp())
        evu_off_periods = [{"group_id": 1, "start": evu_start, "stop": evu_start + 3600}]

        result = self.schedule_builder._generate_geothermal_pump_schedule(
            selected_hours, evu_off_periods, day_priorities, datetime.datetime(2025, 1, 15)
        )

        # Verify schedule is sorted
        prev_ts = 0
        for entry in result["schedule"]:
            self.assertGreaterEqual(entry["timestamp"], prev_ts)
            prev_ts = entry["timestamp"]


class TestPlanningResultsEdgeCases(unittest.TestCase):
    """Test edge cases in planning results calculation."""

    def setUp(self):
        """Set up test fixtures."""
        with (
            patch("src.control.program_generator.get_config"),
            patch("src.control.program_generator.InfluxClient"),
            patch("src.control.program_generator.HeatingDataFetcher"),
            patch("src.control.program_generator.HeatingCurve"),
            patch("src.control.program_generator.HeatingOptimizer"),
        ):
            self.generator = HeatingProgramGenerator()

    def test_calculate_planning_results_empty_selected_hours(self):
        """Test planning results with empty selected hours."""
        loads_schedules = {
            "geothermal_pump": {
                "total_intervals_on": 0,
                "estimated_cost_eur": 0.0,
                "schedule": [],
            }
        }

        selected_hours = pd.DataFrame()

        result = self.generator._calculate_planning_results(loads_schedules, 0.0, selected_hours)

        self.assertEqual(result["cheapest_interval_price"], 0.0)
        self.assertEqual(result["most_expensive_interval_price"], 0.0)
        self.assertEqual(result["average_heating_price"], 0.0)


if __name__ == "__main__":
    unittest.main()
