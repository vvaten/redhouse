#!/usr/bin/env python
"""Build heating schedules for all loads."""

import datetime

from src.common.logger import setup_logger

logger = setup_logger(__name__)


class ScheduleBuilder:
    """
    Build heating schedules for all loads.

    Responsible for:
    - Generating geothermal pump schedules
    - Placeholder for garage heater
    - Placeholder for EV charger
    - Adding ALE (auto mode) transitions
    """

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

    def generate_load_schedules(
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

        pump_schedule = self._generate_geothermal_pump_schedule(
            selected_hours, evu_off_periods, day_priorities, program_date
        )
        loads_schedules["geothermal_pump"] = pump_schedule

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
        heating_hours = sorted(selected_hours.index)

        schedule_entries = self._build_heating_schedule_entries(
            heating_hours, selected_hours, day_priorities, load_config
        )
        schedule_entries.extend(self._build_evu_off_entries(evu_off_periods))
        schedule_entries.sort(key=lambda x: x["timestamp"])

        final_schedule = self._insert_ale_transitions(schedule_entries)
        total_hours_on, total_cost = self._calculate_schedule_statistics(
            final_schedule, heating_hours
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

    def _build_heating_schedule_entries(
        self, heating_hours, selected_hours, day_priorities, load_config
    ) -> list:
        """
        Build schedule entries for heating hours.

        Args:
            heating_hours: List of heating hour timestamps
            selected_hours: Selected heating hours DataFrame
            day_priorities: Full day priorities DataFrame
            load_config: Load configuration dict

        Returns:
            List of schedule entry dicts
        """
        entries = []
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
                        day_priorities.loc[hour, "price_total"] * 100
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
                "priority_score": round(priority_score / load_config["power_kw"] * 100, 2),
                "estimated_cost_eur": round(priority_score, 3),
            }
            entries.append(entry)
        return entries

    def _build_evu_off_entries(self, evu_off_periods) -> list:
        """
        Build schedule entries for EVU-OFF periods.

        Args:
            evu_off_periods: List of EVU-OFF period dicts

        Returns:
            List of EVU-OFF schedule entry dicts
        """
        entries = []
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
            entries.append(entry)
        return entries

    def _insert_ale_transitions(self, schedule_entries) -> list:
        """
        Insert ALE (auto mode) transitions between commands.

        Args:
            schedule_entries: List of schedule entries (sorted by timestamp)

        Returns:
            Final schedule with ALE transitions inserted
        """
        final_schedule = []
        for i, entry in enumerate(schedule_entries):
            final_schedule.append(entry)

            if entry["command"] in ["ON", "EVU"]:
                end_timestamp = entry["timestamp"] + (entry["duration_minutes"] * 60)
                end_dt = datetime.datetime.fromtimestamp(end_timestamp).astimezone()

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

        final_schedule.sort(key=lambda x: x["timestamp"])
        return final_schedule

    def _calculate_schedule_statistics(self, final_schedule, heating_hours) -> tuple:
        """
        Calculate total heating hours and cost from schedule.

        Args:
            final_schedule: Final schedule with all entries
            heating_hours: List of heating hour timestamps

        Returns:
            Tuple of (total_hours_on, total_cost)
        """
        total_hours_on = len(heating_hours)
        total_cost = sum(
            e.get("estimated_cost_eur", 0.0) for e in final_schedule if e.get("estimated_cost_eur")
        )
        return total_hours_on, total_cost
