#!/usr/bin/env python
"""Generate daily heating programs with multi-load support."""

import datetime
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.local_time import HELSINKI, helsinki_date
from src.common.logger import setup_logger
from src.control import program_influx
from src.control import quarter_program as qprog
from src.control.evu_optimizer import EvuOptimizer
from src.control.heating_curve import HeatingCurve
from src.control.heating_data_fetcher import HeatingDataFetcher
from src.control.heating_optimizer import HeatingOptimizer
from src.control.hot_water_planner import lost_heating_hours
from src.control.load_budget import hour_caps
from src.control.program_fallback import load_valid_program
from src.control.program_shape import PUMP_LOAD
from src.control.schedule_builder import ScheduleBuilder
from src.monitoring.program_check import PROGRAM_BASE_DIR, program_path

logger = setup_logger(__name__)
CAP_STATE = Path(__file__).resolve().parents[2] / "data" / "power_cap_state.json"


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

    def __init__(self, config: Optional[dict] = None, program_dir: str = PROGRAM_BASE_DIR):
        """
        Initialize heating program generator.

        Args:
            config: Configuration dict (uses get_config() if None)
            program_dir: Where the day programs are, for the day before's
        """
        self.config = config or get_config()
        self.program_dir = program_dir
        settings: Any = self.config
        self.max_off_h = float(settings.max_off_h)
        self.today_revision: Optional[dict] = None
        self.influx = InfluxClient(self.config)
        self.data_fetcher = HeatingDataFetcher()
        self.heating_curve = HeatingCurve()
        self.optimizer = HeatingOptimizer(
            base_load_kw=1.0,
            heating_load_kw=3.0,
            resolution_minutes=15,
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
        df = self._fetch_and_validate_data(date_offset, program_date.date())
        avg_temperature, hours_to_heat = self._calculate_heating_requirements(df, date_offset)
        hours_to_heat += lost_heating_hours(self.config, self.influx, program_date_str)
        window = self._window(program_date, live=base_date is None and date_offset == 1)
        priorities = self._window_priorities(df, window)
        hours = sorted({qprog.hour_key(t) for t in priorities.index})
        caps = hour_caps(hours, self.config, CAP_STATE)
        n = int(round(hours_to_heat * 4)) + window.carried
        selection = qprog.select(priorities, n, caps, self.max_off_h, window.lead)
        evu_off_periods = self.evu_optimizer.quarter_periods(priorities, n, selection.on_index)
        loads_schedules = self.schedule_builder.generate_load_schedules(
            selection.runs, evu_off_periods, priorities, program_date
        )
        loads_schedules = self._split_today(loads_schedules, window, program_date)
        planning_results = self._calculate_planning_results(
            loads_schedules, hours_to_heat, selection.runs
        )
        planning_results.update(self._comfort_results(selection, window))

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

    def _window(self, program_date: datetime.datetime, live: bool) -> qprog.Window:
        """The day, or with a live run the rest of today and the day together."""
        day_start = HELSINKI.localize(
            datetime.datetime.combine(program_date.date(), datetime.time())
        )
        day_stop = HELSINKI.normalize(day_start + datetime.timedelta(days=1))
        before = load_valid_program(
            program_path(program_date.date() - datetime.timedelta(days=1), self.program_dir)
        )
        now = int(time.time()) if live else None
        window = qprog.plan_window(
            int(day_start.timestamp()), int(day_stop.timestamp()), before, PUMP_LOAD, now
        )
        logger.info(
            f"Window {datetime.datetime.fromtimestamp(window.start, HELSINKI)} to "
            f"{datetime.datetime.fromtimestamp(window.stop, HELSINKI)}: "
            f"{window.carried} ON quarters carried, {window.lead} OFF quarters before it"
        )
        return window

    def _window_priorities(self, df, window: qprog.Window):
        """One priority row per quarter of the window; a quarter without a
        price takes its hour's earlier quarter, none at all is an error."""
        priorities = self.optimizer.calculate_heating_priorities(df)
        start = pd.Timestamp(window.start, unit="s", tz="UTC").tz_convert(HELSINKI)
        stop = pd.Timestamp(window.stop, unit="s", tz="UTC").tz_convert(HELSINKI)
        quarters = pd.date_range(start, stop, freq="15min", inclusive="left")
        priorities = priorities.reindex(quarters).ffill(limit=3)
        missing = priorities.index[priorities["heating_prio"].isna()]
        if len(missing):
            raise ValueError(f"No price for the quarter at {missing[0]}")
        return priorities

    def _split_today(self, loads_schedules: dict, window: qprog.Window, program_date) -> dict:
        """Cut the pump schedule at the day start; the part before goes into
        today_revision, today's program re-planned from the window start."""
        self.today_revision = None
        if window.today is None:
            return loads_schedules
        day_start = HELSINKI.localize(
            datetime.datetime.combine(program_date.date(), datetime.time())
        )
        pump = loads_schedules[PUMP_LOAD]
        today_part, day_part = qprog.split_at(pump["schedule"], int(day_start.timestamp()))
        self.today_revision = qprog.revise_today(window.today, PUMP_LOAD, today_part, window.start)
        return dict(loads_schedules, **{PUMP_LOAD: qprog.with_schedule(pump, day_part)})

    def _comfort_results(self, selection: qprog.Selection, window: qprog.Window) -> dict:
        comfort = selection.cost_eur - selection.free_cost_eur
        logger.info(
            f"Window cost {selection.cost_eur:.2f} EUR, {comfort:.2f} EUR of it for the "
            f"{self.max_off_h} h gap limit (kept: {selection.gap_kept})"
        )
        return {
            "max_off_h": self.max_off_h,
            "gap_limit_kept": selection.gap_kept,
            "window_cost_eur": round(selection.cost_eur, 2),
            "comfort_cost_eur": round(comfort, 2),
            "carried_on_quarters": window.carried,
        }

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
            program_date = datetime.datetime.combine(helsinki_date(date_offset), datetime.time.min)

        program_date_str = program_date.strftime("%Y-%m-%d")
        logger.info(f"Program date: {program_date_str}")
        return program_date_str, program_date

    def _fetch_and_validate_data(self, date_offset: int, program_day: datetime.date):
        """
        Fetch heating data and validate availability.

        Args:
            date_offset: Day offset from today
            program_day: The day the program is for, which the data must cover

        Returns:
            DataFrame with heating data

        Raises:
            ValueError: If no data available
            HeatingDataUnavailable: If prices or forecast miss an hour of the day
        """
        df = self.data_fetcher.fetch_heating_data(
            date_offset=date_offset, lookback_days=1, lookahead_days=2
        )

        if df.empty:
            logger.error("No data fetched, cannot generate program")
            raise ValueError("No data available for program generation")

        self.data_fetcher.require_day_coverage(df, program_day)
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
            "generated_at": datetime.datetime.now(HELSINKI).isoformat(),
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
                "resolution_minutes": self.optimizer.resolution_minutes,
                "max_off_h": self.max_off_h,
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
        points = program_influx.schedule_points(program, program_date, data_type)
        points.append(program_influx.summary_point(program, program_date, data_type))

        try:
            bucket_name = self._get_bucket_name("influxdb_bucket_load_control", "load_control")
            self.influx.write_with_retry(bucket=bucket_name, record=points)
            logger.info(f"Saved {len(points)} points to InfluxDB bucket '{bucket_name}'")
        except Exception as e:
            logger.error(f"Failed to save program to InfluxDB: {e}")
            raise

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
