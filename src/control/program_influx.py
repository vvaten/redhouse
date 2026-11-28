"""InfluxDB points of a heating program, for Grafana: one per schedule
entry and one summary point."""

import datetime

from influxdb_client import Point


def power_kw(command: str, load_power_kw: float) -> float:
    """The load's power while the command runs: its rating in ON, else 0."""
    return load_power_kw if command == "ON" else 0.0


def schedule_points(program: dict, program_date: str, data_type: str) -> list:
    """One load_control point per schedule entry of every load."""
    points = []
    for load_id, load_data in program["loads"].items():
        for entry in load_data["schedule"]:
            timestamp = datetime.datetime.fromtimestamp(
                entry["timestamp"], tz=datetime.timezone.utc
            )
            command = entry["command"]
            point = (
                Point("load_control")
                .tag("program_date", program_date)
                .tag("load_id", load_id)
                .tag("data_type", data_type)
                .field("command", command)
                .field("power_kw", power_kw(command, load_data["power_kw"]))
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


def summary_point(program: dict, program_date: str, data_type: str) -> Point:
    """The load_control_summary point with the planning results."""
    results = program["planning_results"]
    return (
        Point("load_control_summary")
        .tag("program_date", program_date)
        .tag("data_type", data_type)
        .field("avg_temperature_c", program["input_parameters"]["avg_temperature_c"])
        .field("total_heating_hours", results["total_heating_hours_needed"])
        .field("total_cost_eur", results["estimated_total_cost_eur"])
        .field("total_heating_intervals", results["total_heating_intervals_planned"])
        .field("total_evu_off_intervals", results["total_evu_off_intervals"])
        .field("cheapest_price", results["cheapest_interval_price"])
        .field("most_expensive_price", results["most_expensive_interval_price"])
        .field("average_price", results["average_heating_price"])
        .time(datetime.datetime.now(tz=datetime.timezone.utc))
    )
