#!/usr/bin/env python
"""Optimize EVU-OFF periods to block expensive direct heating."""

import math
from typing import Any

from src.common.logger import setup_logger
from src.control.heating_optimizer import HeatingOptimizer

logger = setup_logger(__name__)


class EvuOptimizer:
    """
    Optimize EVU-OFF periods to block expensive direct heating.

    EVU-OFF is used to prevent the heat pump from using expensive
    direct heating during high electricity price periods.

    Responsible for:
    - Identifying expensive hours above threshold
    - Grouping consecutive hours with max continuous length limit
    - Merging adjacent groups when possible
    """

    EVU_OFF_THRESHOLD_PRICE = 15.0
    EVU_OFF_MAX_CONTINUOUS_HOURS = 4

    def __init__(self, optimizer: HeatingOptimizer):
        """
        Initialize EVU optimizer.

        Args:
            optimizer: HeatingOptimizer instance for filtering priorities
        """
        self.optimizer = optimizer

    def generate_evu_off_periods(
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
        evu_off_max_hours = 24 - math.ceil(hours_to_heat) - 2

        if evu_off_max_hours <= 0:
            logger.info("No room for EVU-OFF periods (heating all day)")
            return []

        day_priorities = self.optimizer.filter_day_priorities(priorities_df, date_offset)

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

        evu_off_groups = self._optimize_evu_off_groups(
            expensive_hours, self.EVU_OFF_MAX_CONTINUOUS_HOURS
        )

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
                if hour.timestamp() == group["first"].timestamp() - 3600:
                    duration_hours = (group["last"].timestamp() - group["first"].timestamp()) / 3600
                    if duration_hours < max_continuous_hours - 1:
                        group["first"] = hour
                        extended = True
                        break
                    else:
                        rejected = True
                        break

                elif hour.timestamp() == group["last"].timestamp() + 3600:
                    duration_hours = (group["last"].timestamp() - group["first"].timestamp()) / 3600
                    if duration_hours < max_continuous_hours - 1:
                        group["last"] = hour
                        extended = True
                        break
                    else:
                        rejected = True
                        break

            if not extended and not rejected:
                groups.append({"first": hour, "last": hour})

        sorted_groups = sorted(groups, key=lambda x: x["first"])
        merged_groups = []

        for i, group in enumerate(sorted_groups):
            if i == 0:
                merged_groups.append(group)
                continue

            prev_group = merged_groups[-1]

            if group["first"].timestamp() == prev_group["last"].timestamp() + 3600:
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
