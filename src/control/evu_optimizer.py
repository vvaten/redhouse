#!/usr/bin/env python
"""Optimize EVU-OFF periods to block expensive direct heating."""

import math
from typing import Any, Optional

from src.common.config import get_config
from src.common.logger import setup_logger
from src.control.heating_optimizer import HeatingOptimizer

logger = setup_logger(__name__)


def limit_runs(spans: list, head: Optional[tuple], max_s: int) -> list:
    """The sorted (start, stop) spans with every run of touching spans, the
    head included, at most max_s long: a span that would pass it starts one
    quarter later, or goes when nothing of it is left."""
    out: list = []
    run_start, run_stop = head if head is not None else (None, None)
    for a, b in spans:
        if run_stop == a and b - run_start > max_s:
            a += 900
        if a >= b:
            continue
        if run_stop != a:
            run_start = a
        out.append((a, b))
        run_stop = b
    return out


class EvuOptimizer:
    """
    Optimize EVU-OFF periods to block expensive direct heating.

    EVU-OFF is used to prevent the heat pump from using expensive
    direct heating during high electricity price periods.

    Responsible for:
    - Identifying expensive hours above threshold (price_total in EUR/kWh)
    - Grouping consecutive hours with max continuous length limit
    - Merging adjacent groups when possible
    """

    def __init__(self, optimizer: HeatingOptimizer):
        """
        Initialize EVU optimizer.

        Args:
            optimizer: HeatingOptimizer instance for filtering priorities
        """
        self.optimizer = optimizer
        config = get_config()
        self.EVU_OFF_THRESHOLD_PRICE = config.evuoff_threshold_price
        self.EVU_OFF_MAX_CONTINUOUS_HOURS = config.evuoff_max_continuous_hours

    def generate_evu_off_periods(
        self, df, priorities_df, hours_to_heat: float, date_offset: int, keep=None
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
        day_priorities = self.optimizer.filter_day_priorities(priorities_df, date_offset)

        # One row per hour; the day has 23, 24 or 25 hours
        evu_off_max_hours = len(day_priorities) - math.ceil(hours_to_heat) - 2

        if evu_off_max_hours <= 0:
            logger.info("No room for EVU-OFF periods (heating all day)")
            return []

        expensive_hours = day_priorities[
            day_priorities["price_total"] > self.EVU_OFF_THRESHOLD_PRICE
        ].sort_values(
            by="price_total", ascending=False
        )  # type: ignore[call-overload]

        if keep is not None:
            # Heating hours the power budget chose stay out of the EVU blocks
            expensive_hours = expensive_hours[~expensive_hours.index.isin(keep)]
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

    def quarter_periods(
        self, priorities, on_quarters: int, keep, running_from: Optional[int] = None
    ) -> list[dict]:
        """EVU-OFF blocks over a window of quarter rows: quarters dearer than
        the threshold and not ON, leaving 2 h of the window out of EVU besides
        the ON quarters. running_from: start of an EVU block that runs into it."""
        room = len(priorities) - on_quarters - 8
        if room <= 0:
            logger.info("No room for EVU-OFF periods (heating most of the window)")
            return []
        dear = priorities[priorities["price_total"] > self.EVU_OFF_THRESHOLD_PRICE]
        dear = dear[~dear.index.isin(keep)].sort_values(by="price_total", ascending=False)
        dear = dear.head(room)
        if dear.empty:
            logger.info("No quarters expensive enough for EVU-OFF")
            return []
        groups = self._optimize_evu_off_groups(dear, self.EVU_OFF_MAX_CONTINUOUS_HOURS * 4, 900)
        spans = [
            (int(g["first"].timestamp()), int(g["last"].timestamp()) + 900)
            for g in sorted(groups, key=lambda g: g["first"])
        ]
        start = int(priorities.index[0].timestamp())
        head = None if running_from is None else (running_from, start)
        periods = []
        for group_id, (a, b) in enumerate(limit_runs(spans, head, self._max_evu_s()), start=1):
            periods.append({"group_id": group_id, "start": a, "stop": b})
            logger.info(f"EVU-OFF group {group_id}: from {a} for {(b - a) // 60} min")
        return periods

    def _max_evu_s(self) -> int:
        return int(self.EVU_OFF_MAX_CONTINUOUS_HOURS * 3600)

    def _optimize_evu_off_groups(
        self, expensive_hours_df, max_continuous_hours: int, step: int = 3600
    ) -> list[dict]:
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
                if hour.timestamp() == group["first"].timestamp() - step:
                    duration_hours = (group["last"].timestamp() - group["first"].timestamp()) / step
                    if duration_hours < max_continuous_hours - 1:
                        group["first"] = hour
                        extended = True
                        break
                    else:
                        rejected = True
                        break

                elif hour.timestamp() == group["last"].timestamp() + step:
                    duration_hours = (group["last"].timestamp() - group["first"].timestamp()) / step
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

            if group["first"].timestamp() == prev_group["last"].timestamp() + step:
                merged_duration = (
                    group["last"].timestamp() - prev_group["first"].timestamp()
                ) / step
                if merged_duration <= max_continuous_hours - 1:
                    prev_group["last"] = group["last"]
                    continue

            merged_groups.append(group)

        logger.info(
            f"Optimized {len(expensive_hours_df)} hours into {len(merged_groups)} EVU-OFF groups"
        )

        return merged_groups
