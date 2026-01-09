#!/usr/bin/env python
"""Generate daily heating programs with multi-load support."""

import datetime
import json
import math
import os
from typing import Any, Optional

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger
from src.control.heating_curve import HeatingCurve
from src.control.heating_data_fetcher import HeatingDataFetcher
from src.control.heating_optimizer import HeatingOptimizer

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

    # Load definitions
    LOADS = {
        "geothermal_pump": {
            "priority": 1,
            "power_kw": 3.0,
            "control_type": "mlp_i2c",
            "enabled": True,
        },
        "garage_heater": {
            "priority": 2,
            "power_kw": 2.0,
            "control_type": "shelly_relay",
            "enabled": False,
        },
        "ev_charger": {
            "priority": 3,
            "power_kw": 11.0,
            "control_type": "ocpp",
            "enabled": False,
        },
    }

    # EVU-OFF configuration
    EVU_OFF_THRESHOLD_PRICE = 15.0  # c/kWh
    EVU_OFF_MAX_CONTINUOUS_HOURS = 4

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

        if base_date:
            program_date = datetime.datetime.strptime(base_date, "%Y-%m-%d") + datetime.timedelta(
                days=date_offset
            )
        else:
            program_date = datetime.datetime.now() + datetime.timedelta(days=date_offset)

        program_date_str = program_date.strftime("%Y-%m-%d")
        logger.info(f"Program date: {program_date_str}")

        # Fetch data
        df = self.data_fetcher.fetch_heating_data(
            date_offset=date_offset, lookback_days=1, lookahead_days=2
        )

        if df.empty:
            logger.error("No data fetched, cannot generate program")
            raise ValueError("No data available for program generation")

        # Calculate heating requirements
        avg_temperature = self.data_fetcher.get_day_average_temperature(df, date_offset)
        hours_to_heat = self.heating_curve.calculate_heating_hours(avg_temperature)

        logger.info(f"Average temperature: {avg_temperature:.1f}C")
        logger.info(f"Required heating hours: {hours_to_heat:.2f}h")

        # Calculate heating priorities
        priorities_df = self.optimizer.calculate_heating_priorities(df)
        day_priorities = self.optimizer.filter_day_priorities(priorities_df, date_offset)

        if day_priorities.empty:
            logger.error("No priority data for the target day")
            raise ValueError("No priority data available")

        # Select cheapest hours for heating
        selected_hours = self.optimizer.select_cheapest_hours(day_priorities, hours_to_heat)

        # Calculate EVU-OFF periods
        evu_off_periods = self._generate_evu_off_periods(
            df, priorities_df, hours_to_heat, date_offset
        )

        # Generate schedules for each load
        loads_schedules = self._generate_load_schedules(
            selected_hours, evu_off_periods, day_priorities, program_date
        )

        # Calculate planning results
        planning_results = self._calculate_planning_results(
            loads_schedules, hours_to_heat, selected_hours
        )

        # Build complete program
        program = {
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
                "evu_off_threshold_price": self.EVU_OFF_THRESHOLD_PRICE,
                "evu_off_max_continuous_hours": self.EVU_OFF_MAX_CONTINUOUS_HOURS,
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

        logger.info(f"Generated program with {len(loads_schedules)} loads")

        return program

    def _generate_evu_off_periods(
        self, df, priorities_df, hours_to_heat: float, date_offset: int
    ) -> list[dict]:
        """
        Generate EVU-OFF periods to block expensive direct heating.

        EVU-OFF is used to prevent the heat pump from using expensive
        direct heating during high electricity price periods.

        Args:
            df: Raw data DataFrame
            priorities_df: Heating priorities DataFrame
            hours_to_heat: Total heating hours needed
            date_offset: Day offset

        Returns:
            List of EVU-OFF period dicts with start/stop timestamps
        """
        # Calculate maximum hours we can block
        evu_off_max_hours = 24 - math.ceil(hours_to_heat) - 2

        if evu_off_max_hours <= 0:
            logger.info("No room for EVU-OFF periods (heating all day)")
            return []

        # Filter to target day
        day_priorities = self.optimizer.filter_day_priorities(priorities_df, date_offset)

        # Find expensive hours above threshold
        expensive_hours = day_priorities[
            day_priorities["heating_prio"] > self.EVU_OFF_THRESHOLD_PRICE
        ].sort_values(
            by="heating_prio", ascending=False
        )  # type: ignore[call-overload]

        expensive_hours = expensive_hours.head(evu_off_max_hours)

        if expensive_hours.empty:
            logger.info("No hours expensive enough for EVU-OFF")
            return []

        logger.info(f"Found {len(expensive_hours)} expensive hours for EVU-OFF consideration")

        # Group consecutive hours (max 4 hours continuous)
        evu_off_groups = self._optimize_evu_off_groups(
            expensive_hours, self.EVU_OFF_MAX_CONTINUOUS_HOURS
        )

        # Convert to timestamp format
        evu_off_periods = []
        for group_id, group in enumerate(evu_off_groups, start=1):
            start_ts = int(group["first"].timestamp())
            stop_ts = int(group["last"].timestamp()) + 3600

            evu_off_periods.append({"group_id": group_id, "start": start_ts, "stop": stop_ts})

            logger.info(
                f"EVU-OFF group {group_id}: {group['first']} to {group['last']} "
                f"({(stop_ts - start_ts) / 3600:.0f} hours)"
            )

        return evu_off_periods

    def _optimize_evu_off_groups(self, expensive_hours_df, max_continuous_hours: int) -> list[dict]:
        """
        Optimize EVU-OFF hours into groups with maximum continuous length.

        Args:
            expensive_hours_df: DataFrame of expensive hours
            max_continuous_hours: Maximum hours in a continuous block

        Returns:
            List of groups with 'first' and 'last' timestamps
        """
        groups: list[dict[str, Any]] = []

        for hour in expensive_hours_df.index:
            if not groups:
                groups.append({"first": hour, "last": hour})
                continue

            extended = False
            rejected = False

            for group in groups:
                # Check if hour extends group from beginning
                if hour.timestamp() == group["first"].timestamp() - 3600:
                    duration_hours = (group["last"].timestamp() - group["first"].timestamp()) / 3600
                    if duration_hours < max_continuous_hours - 1:
                        group["first"] = hour
                        extended = True
                        break
                    else:
                        rejected = True
                        break

                # Check if hour extends group from end
                elif hour.timestamp() == group["last"].timestamp() + 3600:
                    duration_hours = (group["last"].timestamp() - group["first"].timestamp()) / 3600
                    if duration_hours < max_continuous_hours - 1:
                        group["last"] = hour
                        extended = True
                        break
                    else:
                        rejected = True
                        break

            # Add as new group if not extended or rejected
            if not extended and not rejected:
                groups.append({"first": hour, "last": hour})

        # Merge adjacent groups if they fit within max length
        sorted_groups = sorted(groups, key=lambda x: x["first"])
        merged_groups = []

        for i, group in enumerate(sorted_groups):
            if i == 0:
                merged_groups.append(group)
                continue

            prev_group = merged_groups[-1]

            # Check if groups are adjacent
            if group["first"].timestamp() == prev_group["last"].timestamp() + 3600:
                # Check if merged duration would be acceptable
                merged_duration = (
                    group["last"].timestamp() - prev_group["first"].timestamp()
                ) / 3600
                if merged_duration <= max_continuous_hours - 1:
                    prev_group["last"] = group["last"]
                    continue

            merged_groups.append(group)

        logger.info(
            f"Optimized {len(expensive_hours_df)} hours into {len(merged_groups)} EVU-OFF groups"
        )

        return merged_groups

    def _generate_load_schedules(
        self, selected_hours, evu_off_periods, day_priorities, program_date
    ) -> dict:
        """
        Generate schedules for all loads.

        Args:
            selected_hours: Selected heating hours DataFrame
            evu_off_periods: EVU-OFF period dicts
            day_priorities: Full day priorities DataFrame
            program_date: Date of the program

        Returns:
            Dict of load schedules keyed by load_id
        """
        loads_schedules = {}

        # Generate geothermal pump schedule
        pump_schedule = self._generate_geothermal_pump_schedule(
            selected_hours, evu_off_periods, day_priorities, program_date
        )
        loads_schedules["geothermal_pump"] = pump_schedule

        # Placeholder for future loads
        for load_id, load_config in self.LOADS.items():
            if load_id == "geothermal_pump":
                continue

            if not load_config["enabled"]:
                loads_schedules[load_id] = {
                    "load_id": load_id,
                    "priority": load_config["priority"],
                    "power_kw": load_config["power_kw"],
                    "control_type": load_config["control_type"],
                    "total_intervals_on": 0,
                    "total_hours_on": 0.0,
                    "estimated_cost_eur": 0.0,
                    "schedule": [],
                }

        return loads_schedules

    def _generate_geothermal_pump_schedule(
        self, selected_hours, evu_off_periods, day_priorities, program_date
    ) -> dict:
        """
        Generate schedule for geothermal heat pump.

        Args:
            selected_hours: Selected heating hours DataFrame
            evu_off_periods: EVU-OFF period dicts
            day_priorities: Full day priorities DataFrame
            program_date: Date of the program

        Returns:
            Load schedule dict
        """
        load_config = self.LOADS["geothermal_pump"]
        schedule_entries = []

        # Sort heating hours by time
        heating_hours = sorted(selected_hours.index)

        # Add heating ON commands
        for hour in heating_hours:
            timestamp = int(hour.timestamp())
            priority_score = selected_hours.loc[hour, "heating_prio"]

            entry = {
                "timestamp": timestamp,
                "utc_time": hour.tz_convert("UTC").isoformat(),
                "local_time": hour.isoformat(),
                "command": "ON",
                "duration_minutes": 60,
                "reason": "cheap_electricity",
                "spot_price_total_c_kwh": round(
                    (
                        day_priorities.loc[hour, "price_total"]
                        if hour in day_priorities.index
                        else 0.0
                    ),
                    2,
                ),
                "solar_prediction_kwh": round(
                    (
                        day_priorities.loc[hour, "solar_yield_avg_prediction"]
                        if hour in day_priorities.index
                        else 0.0
                    ),
                    2,
                ),
                "priority_score": round(priority_score, 2),
                "estimated_cost_eur": round(priority_score * load_config["power_kw"] / 100, 3),
            }

            schedule_entries.append(entry)

        # Add EVU-OFF periods
        for period in evu_off_periods:
            start_dt = datetime.datetime.fromtimestamp(period["start"]).astimezone()
            duration_minutes = int((period["stop"] - period["start"]) / 60)

            entry = {
                "timestamp": period["start"],
                "utc_time": start_dt.astimezone(datetime.timezone.utc).isoformat(),
                "local_time": start_dt.isoformat(),
                "command": "EVU",
                "duration_minutes": duration_minutes,
                "reason": "expensive_direct_heating_blocked",
                "spot_price_total_c_kwh": None,
                "solar_prediction_kwh": None,
                "priority_score": None,
                "evu_off_group_id": period["group_id"],
            }

            schedule_entries.append(entry)

        # Sort by timestamp
        schedule_entries.sort(key=lambda x: x["timestamp"])

        # Add ALE (auto mode) transitions
        final_schedule = []
        for i, entry in enumerate(schedule_entries):
            final_schedule.append(entry)

            # Add ALE after ON or EVU periods
            if entry["command"] in ["ON", "EVU"]:
                # Calculate end time
                end_timestamp = entry["timestamp"] + (entry["duration_minutes"] * 60)
                end_dt = datetime.datetime.fromtimestamp(end_timestamp).astimezone()

                # Don't add ALE if next command is immediate
                next_starts_immediately = False
                if i + 1 < len(schedule_entries):
                    next_timestamp = schedule_entries[i + 1]["timestamp"]
                    if next_timestamp == end_timestamp:
                        next_starts_immediately = True

                if not next_starts_immediately:
                    ale_entry = {
                        "timestamp": end_timestamp,
                        "utc_time": end_dt.astimezone(datetime.timezone.utc).isoformat(),
                        "local_time": end_dt.isoformat(),
                        "command": "ALE",
                        "duration_minutes": None,
                        "reason": (
                            "heating_complete" if entry["command"] == "ON" else "evu_off_complete"
                        ),
                    }
                    final_schedule.append(ale_entry)

        # Sort again after adding ALE entries
        final_schedule.sort(key=lambda x: x["timestamp"])

        # Calculate totals
        total_hours_on = len(heating_hours)
        total_cost = sum(
            e.get("estimated_cost_eur", 0.0) for e in final_schedule if e.get("estimated_cost_eur")
        )

        return {
            "load_id": "geothermal_pump",
            "priority": load_config["priority"],
            "power_kw": load_config["power_kw"],
            "control_type": load_config["control_type"],
            "total_intervals_on": len(heating_hours),
            "total_hours_on": float(total_hours_on),
            "estimated_cost_eur": round(total_cost, 2),
            "schedule": final_schedule,
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
        if not selected_hours.empty:
            cheapest_price = selected_hours["heating_prio"].min()
            most_expensive_price = selected_hours["heating_prio"].max()
            avg_price = selected_hours["heating_prio"].mean()
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
        from influxdb_client import Point

        program_date = program["program_date"]
        points = []

        # Write schedule entries for each load
        for load_id, load_data in program["loads"].items():
            for entry in load_data["schedule"]:
                timestamp = datetime.datetime.fromtimestamp(entry["timestamp"])
                command = entry["command"]

                # Determine if load is ON (for easy Grafana plotting)
                is_on = command == "ON"
                is_evu_off = command == "EVU"
                power_kw = load_data["power_kw"] if is_on else 0.0

                point = (
                    Point("load_control")
                    .tag("program_date", program_date)
                    .tag("load_id", load_id)
                    .tag("data_type", data_type)
                    .field("command", command)
                    .field("power_kw", power_kw)
                    .field("is_on", is_on)
                    .field("is_evu_off", is_evu_off)
                    .field("priority_score", entry.get("priority_score") or 0.0)
                    .field("spot_price_c_kwh", entry.get("spot_price_total_c_kwh") or 0.0)
                    .field("solar_prediction_kwh", entry.get("solar_prediction_kwh") or 0.0)
                    .field("estimated_cost_eur", entry.get("estimated_cost_eur") or 0.0)
                    .field("duration_minutes", entry.get("duration_minutes") or 0)
                    .field("reason", entry.get("reason", "unknown"))
                    .time(timestamp)
                )

                points.append(point)

        # Write summary to load_control_summary measurement
        summary_point = (
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

        points.append(summary_point)

        # Write all points
        try:
            # Get bucket name - supports both Config object and dict
            if hasattr(self.config, "influxdb_bucket_load_control"):
                bucket_name = self.config.influxdb_bucket_load_control
            else:
                bucket_name = self.config.get("influxdb_bucket_load_control")

            if not bucket_name:
                logger.error(
                    "INFLUXDB_BUCKET_LOAD_CONTROL is not configured! "
                    "Set it in .env to 'load_control_staging' (staging) or 'load_control' (production)"
                )
                raise ValueError("Missing required configuration: INFLUXDB_BUCKET_LOAD_CONTROL")

            self.influx.write_api.write(bucket=bucket_name, record=points)
            logger.info(f"Saved {len(points)} points to InfluxDB bucket '{bucket_name}'")
        except Exception as e:
            logger.error(f"Failed to save program to InfluxDB: {e}")
            raise
