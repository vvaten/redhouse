#!/usr/bin/env python
"""Generate daily heating programs with multi-load support."""

import datetime
import json
import os
from typing import Optional

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger
from src.control.evu_optimizer import EvuOptimizer
from src.control.heating_curve import HeatingCurve
from src.control.heating_data_fetcher import HeatingDataFetcher
from src.control.heating_optimizer import HeatingOptimizer
from src.control.schedule_builder import ScheduleBuilder

logger = setup_logger(__name__)


class HeatingProgramGenerator:
    """
    Generate daily heating programs optimized for cost and comfort.

    Supports multiple loads with different priorities:
    - Geothermal heat pump (priority 1, 3kW)
    - Garage heater (priority 2, 2kW)
    - EV charger (priority 3, 11kW)

    Features:
    - Cost-optimized heating schedules
    - EVU-OFF periods to block expensive direct heating
    - Multi-load coordination with power limits
    - Full simulation support with historical data
    - Plan vs actual tracking in InfluxDB
    """

    VERSION = "2.0.0"

    def __init__(self, config: Optional[dict] = None):
        """
        Initialize heating program generator.

        Args:
            config: Configuration dict (uses get_config() if None)
        """
        self.config = config or get_config()
        self.influx = InfluxClient(self.config)
        self.data_fetcher = HeatingDataFetcher()
        self.heating_curve = HeatingCurve()
        self.optimizer = HeatingOptimizer(
            base_load_kw=1.0,
            heating_load_kw=3.0,
            resolution_minutes=60,
        )
        self.evu_optimizer = EvuOptimizer(self.optimizer)
        self.schedule_builder = ScheduleBuilder()

        logger.info(f"Initialized HeatingProgramGenerator v{self.VERSION}")

    def generate_daily_program(
        self,
        date_offset: int = 1,
        simulation_mode: bool = False,
        base_date: Optional[str] = None,
    ) -> dict:
        """
        Generate complete heating program for a day.

        Args:
            date_offset: Day offset from today (1 = tomorrow, 0 = today)
            simulation_mode: If True, marks as simulation (doesn't write to production DB)
            base_date: Base date for historical simulation (YYYY-MM-DD format)

        Returns:
            Complete program dict with all loads and schedules
        """
        logger.info(f"Generating heating program for date_offset={date_offset}")

        program_date_str, program_date = self._calculate_program_date(base_date, date_offset)
        df = self._fetch_and_validate_data(date_offset)
        avg_temperature, hours_to_heat = self._calculate_heating_requirements(df, date_offset)
        day_priorities = self._calculate_and_filter_priorities(df, date_offset)

        # Select cheapest hours for heating
        selected_hours = self.optimizer.select_cheapest_hours(day_priorities, hours_to_heat)

        # Calculate EVU-OFF periods
        evu_off_periods = self.evu_optimizer.generate_evu_off_periods(
            df, self.optimizer.calculate_heating_priorities(df), hours_to_heat, date_offset
        )

        # Generate schedules for each load
        loads_schedules = self.schedule_builder.generate_load_schedules(
            selected_hours, evu_off_periods, day_priorities, program_date
        )

        # Calculate planning results
        planning_results = self._calculate_planning_results(
            loads_schedules, hours_to_heat, selected_hours
        )

        program = self._build_program_structure(
            program_date_str,
            avg_temperature,
            hours_to_heat,
            planning_results,
            loads_schedules,
            date_offset,
            simulation_mode,
            base_date,
        )

        logger.info(f"Generated program with {len(loads_schedules)} loads")

        return program

    def _calculate_program_date(self, base_date: Optional[str], date_offset: int) -> tuple:
        """
        Calculate and format program date.

        Args:
            base_date: Base date for historical simulation (YYYY-MM-DD format)
            date_offset: Day offset from base date or today

        Returns:
            Tuple of (program_date_str, program_date)
        """
        if base_date:
            program_date = datetime.datetime.strptime(base_date, "%Y-%m-%d") + datetime.timedelta(
                days=date_offset
            )
        else:
            program_date = datetime.datetime.now() + datetime.timedelta(days=date_offset)

        program_date_str = program_date.strftime("%Y-%m-%d")
        logger.info(f"Program date: {program_date_str}")
        return program_date_str, program_date

    def _fetch_and_validate_data(self, date_offset: int):
        """
        Fetch heating data and validate availability.

        Args:
            date_offset: Day offset from today

        Returns:
            DataFrame with heating data

        Raises:
            ValueError: If no data available
        """
        df = self.data_fetcher.fetch_heating_data(
            date_offset=date_offset, lookback_days=1, lookahead_days=2
        )

        if df.empty:
            logger.error("No data fetched, cannot generate program")
            raise ValueError("No data available for program generation")

        return df

    def _calculate_heating_requirements(self, df, date_offset: int) -> tuple:
        """
        Calculate required heating hours from temperature and curve.

        Args:
            df: DataFrame with heating data
            date_offset: Day offset from today

        Returns:
            Tuple of (avg_temperature, hours_to_heat)
        """
        avg_temperature = self.data_fetcher.get_day_average_temperature(df, date_offset)
        hours_to_heat = self.heating_curve.calculate_heating_hours(avg_temperature)

        logger.info(f"Average temperature: {avg_temperature:.1f}C")
        logger.info(f"Required heating hours: {hours_to_heat:.2f}h")

        return avg_temperature, hours_to_heat

    def _calculate_and_filter_priorities(self, df, date_offset: int):
        """
        Calculate priorities and filter to target day.

        Args:
            df: DataFrame with heating data
            date_offset: Day offset from today

        Returns:
            Filtered priorities DataFrame for the target day

        Raises:
            ValueError: If no priority data available for target day
        """
        priorities_df = self.optimizer.calculate_heating_priorities(df)
        day_priorities = self.optimizer.filter_day_priorities(priorities_df, date_offset)

        if day_priorities.empty:
            logger.error("No priority data for the target day")
            raise ValueError("No priority data available")

        return day_priorities

    def _build_program_structure(
        self,
        program_date_str: str,
        avg_temperature: float,
        hours_to_heat: float,
        planning_results: dict,
        loads_schedules: dict,
        date_offset: int,
        simulation_mode: bool,
        base_date: Optional[str],
    ) -> dict:
        """
        Assemble complete program structure with metadata.

        Args:
            program_date_str: Program date string (YYYY-MM-DD)
            avg_temperature: Average temperature for the day
            hours_to_heat: Required heating hours
            planning_results: Planning results dict
            loads_schedules: Load schedules dict
            date_offset: Day offset from today
            simulation_mode: Whether in simulation mode
            base_date: Base date for historical simulation

        Returns:
            Complete program dict
        """
        return {
            "version": self.VERSION,
            "generated_at": datetime.datetime.now().astimezone().isoformat(),
            "generator_version": f"redhouse-{self.VERSION}",
            "program_date": program_date_str,
            "input_parameters": {
                "date_offset": date_offset,
                "avg_temperature_c": round(avg_temperature, 2),
                "heating_curve": self.heating_curve.get_curve_points(),
                "base_load_kw": self.optimizer.base_load_kw,
                "heating_load_kw": self.optimizer.heating_load_kw,
                "evu_off_threshold_price": self.evu_optimizer.EVU_OFF_THRESHOLD_PRICE,
                "evu_off_max_continuous_hours": self.evu_optimizer.EVU_OFF_MAX_CONTINUOUS_HOURS,
            },
            "planning_results": planning_results,
            "loads": loads_schedules,
            "simulation_data": (
                {
                    "mode": "historical" if base_date else "live",
                    "base_date": base_date,
                    "data_sources": {
                        "weather": "influxdb",
                        "spot_prices": "influxdb",
                        "solar_predictions": "influxdb",
                    },
                }
                if simulation_mode or base_date
                else None
            ),
            "execution_status": {
                "executed_intervals": 0,
                "pending_intervals": planning_results.get("total_heating_intervals_planned", 0),
                "last_executed_timestamp": None,
                "next_execution_timestamp": None,
            },
        }

    def _calculate_planning_results(self, loads_schedules, hours_to_heat, selected_hours) -> dict:
        """
        Calculate summary statistics for the program.

        Args:
            loads_schedules: All load schedules
            hours_to_heat: Required heating hours
            selected_hours: Selected heating hours DataFrame

        Returns:
            Planning results dict
        """
        total_cost = sum(load["estimated_cost_eur"] for load in loads_schedules.values())
        total_intervals = sum(load["total_intervals_on"] for load in loads_schedules.values())

        # Get price range from selected hours
        # heating_prio is total cost in EUR for the interval, so divide by power to get EUR/kWh
        # then multiply by 100 to convert to c/kWh
        if not selected_hours.empty:
            # Get the heating load power (assume first load in loads_schedules)
            heating_power_kw = next(iter(loads_schedules.values()))["power_kw"]
            cheapest_price = selected_hours["heating_prio"].min() / heating_power_kw * 100
            most_expensive_price = selected_hours["heating_prio"].max() / heating_power_kw * 100
            avg_price = selected_hours["heating_prio"].mean() / heating_power_kw * 100
        else:
            cheapest_price = 0.0
            most_expensive_price = 0.0
            avg_price = 0.0

        # Count EVU-OFF intervals
        evu_off_intervals = sum(
            len([e for e in load["schedule"] if e.get("command") == "EVU"])
            for load in loads_schedules.values()
        )

        return {
            "total_heating_hours_needed": round(hours_to_heat, 2),
            "total_heating_intervals_planned": total_intervals,
            "total_evu_off_intervals": evu_off_intervals,
            "estimated_total_cost_eur": round(total_cost, 2),
            "estimated_heating_cost_eur": round(total_cost, 2),
            "estimated_base_load_cost_eur": 0.0,
            "cheapest_interval_price": round(cheapest_price, 2),
            "most_expensive_interval_price": round(most_expensive_price, 2),
            "average_heating_price": round(avg_price, 2),
        }

    def save_program_json(self, program: dict, output_dir: str = ".") -> str:
        """
        Save program to JSON file.

        Args:
            program: Program dict from generate_daily_program()
            output_dir: Output directory (default: current directory)

        Returns:
            Path to saved file
        """
        program_date = program["program_date"]
        year_month = program_date[:7]  # YYYY-MM

        # Create year-month folder
        folder_path = os.path.join(output_dir, year_month)
        os.makedirs(folder_path, exist_ok=True)

        # Save file
        filename = f"heating_program_schedule_{program_date}.json"
        filepath = os.path.join(folder_path, filename)

        with open(filepath, "w") as f:
            json.dump(program, f, indent=2)

        logger.info(f"Saved program to {filepath}")

        return filepath

    def save_program_influxdb(self, program: dict, data_type: str = "plan"):
        """
        Save program to InfluxDB for Grafana visualization.

        Writes to load_control bucket with data_type tag to distinguish
        between plan/actual/adjusted data.

        Args:
            program: Program dict from generate_daily_program()
            data_type: "plan" | "actual" | "adjusted"
        """
        program_date = program["program_date"]
        points = self._build_schedule_points(program, program_date, data_type)
        summary_point = self._build_summary_point(program, program_date, data_type)
        points.append(summary_point)

        try:
            bucket_name = self._get_bucket_name("influxdb_bucket_load_control", "load_control")
            self.influx.write_api.write(bucket=bucket_name, record=points)
            logger.info(f"Saved {len(points)} points to InfluxDB bucket '{bucket_name}'")
        except Exception as e:
            logger.error(f"Failed to save program to InfluxDB: {e}")
            raise

    def _build_schedule_points(self, program: dict, program_date: str, data_type: str) -> list:
        """
        Build InfluxDB points for all schedule entries.

        Args:
            program: Program dict
            program_date: Program date string
            data_type: Type of data (plan/actual/adjusted)

        Returns:
            List of InfluxDB points
        """
        from influxdb_client import Point

        points = []
        for load_id, load_data in program["loads"].items():
            for entry in load_data["schedule"]:
                timestamp = datetime.datetime.fromtimestamp(entry["timestamp"])
                command = entry["command"]
                power_kw = self._calculate_power_kw(command, load_data["power_kw"])

                point = (
                    Point("load_control")
                    .tag("program_date", program_date)
                    .tag("load_id", load_id)
                    .tag("data_type", data_type)
                    .field("command", command)
                    .field("power_kw", power_kw)
                    .field("is_on", command == "ON")
                    .field("is_evu_off", command == "EVU")
                    .field("priority_score", entry.get("priority_score") or 0.0)
                    .field("spot_price_c_kwh", entry.get("spot_price_total_c_kwh") or 0.0)
                    .field("solar_prediction_kwh", entry.get("solar_prediction_kwh") or 0.0)
                    .field("estimated_cost_eur", entry.get("estimated_cost_eur") or 0.0)
                    .field("duration_minutes", entry.get("duration_minutes") or 0)
                    .field("reason", entry.get("reason", "unknown"))
                    .time(timestamp)
                )
                points.append(point)
        return points

    def _build_summary_point(self, program: dict, program_date: str, data_type: str):
        """
        Build InfluxDB summary point with planning results.

        Args:
            program: Program dict
            program_date: Program date string
            data_type: Type of data (plan/actual/adjusted)

        Returns:
            InfluxDB point for summary
        """
        from influxdb_client import Point

        return (
            Point("load_control_summary")
            .tag("program_date", program_date)
            .tag("data_type", data_type)
            .field("avg_temperature_c", program["input_parameters"]["avg_temperature_c"])
            .field("total_heating_hours", program["planning_results"]["total_heating_hours_needed"])
            .field("total_cost_eur", program["planning_results"]["estimated_total_cost_eur"])
            .field(
                "total_heating_intervals",
                program["planning_results"]["total_heating_intervals_planned"],
            )
            .field(
                "total_evu_off_intervals", program["planning_results"]["total_evu_off_intervals"]
            )
            .field("cheapest_price", program["planning_results"]["cheapest_interval_price"])
            .field(
                "most_expensive_price", program["planning_results"]["most_expensive_interval_price"]
            )
            .field("average_price", program["planning_results"]["average_heating_price"])
            .time(datetime.datetime.now())
        )

    def _get_bucket_name(self, bucket_key: str, default: str) -> str:
        """
        Get bucket name from config (supports both Config object and dict).

        Args:
            bucket_key: Configuration key for bucket name
            default: Default bucket name if not configured

        Returns:
            Bucket name

        Raises:
            ValueError: If bucket name is not configured
        """
        if hasattr(self.config, bucket_key):
            bucket_name = getattr(self.config, bucket_key)
        else:
            bucket_name = self.config.get(bucket_key)

        if not bucket_name:
            logger.error(
                f"{bucket_key.upper()} is not configured! "
                f"Set it in .env to '{default}_staging' (staging) or '{default}' (production)"
            )
            raise ValueError(f"Missing required configuration: {bucket_key.upper()}")

        return bucket_name

    def _calculate_power_kw(self, command: str, load_power_kw: float) -> float:
        """
        Calculate power in kW based on command state.

        Args:
            command: Command string (ON, EVU, ALE, etc.)
            load_power_kw: Power rating of the load in kW

        Returns:
            Power in kW (0.0 if not ON)
        """
        return load_power_kw if command == "ON" else 0.0
