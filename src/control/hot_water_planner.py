"""The hot water boost in the day plan: its heater share in the hour budget
(boost_budget) and the planner link (planner_link). Both are off at first.

The budget share is the only protection against a boost plus a car peak: the
load guard's ON to ALE shed leaves the boost's heater running.
"""

import datetime
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from src.common.atomic_json import write_json
from src.common.logger import setup_logger
from src.control import hot_water_boost as hb

logger = setup_logger(__name__)

# The heater part of a boost: about 100 min from its start
BUDGET_SPAN_S = 120 * 60
GIVEN_BACK = Path(__file__).resolve().parents[2] / "data" / "hot_water_given_back.json"
LOOKBACK_S = 24 * 3600
KEEP_DAYS = 7


@dataclass(frozen=True)
class BoostBudget:
    boost_kw: float
    boost: hb.BoostSettings


def boost_settings(section: dict) -> hb.BoostSettings:
    return hb.load_boost_settings(section["boost"], hb.read_anchor(hb.ANCHOR_FILE))


def boost_budget(section: dict) -> Optional[BoostBudget]:
    """The budget share when hot_water.boost_budget is on, else None."""
    for key in ("boost_budget", "boost_kw", "boost"):
        if key not in section:
            raise ValueError(f"config.yaml hot_water section lacks {key}")
    if not bool(section["boost_budget"]):
        return None
    return BoostBudget(float(section["boost_kw"]), boost_settings(section))


def boost_share_kw(hour_start: datetime.datetime, budget: Optional[BoostBudget]) -> float:
    """boost_kw times the share of the hour inside a boost's first 120 min."""
    if budget is None:
        return 0.0
    start = int(hour_start.timestamp())
    stop = start + 3600
    overlap = sum(
        min(stop, b) - max(start, a)
        for a, b in hb.slots_between(start, stop, budget.boost, BUDGET_SPAN_S)
    )
    return budget.boost_kw * overlap / 3600


def _aware(t) -> datetime.datetime:
    stamp = pd.Timestamp(t)
    if stamp.tzinfo is None:
        raise ValueError(f"The priorities index must carry its zone: {t}")
    return stamp.to_pydatetime()


def slot_last(priorities: pd.DataFrame, boost: hb.BoostSettings) -> pd.DataFrame:
    """Hours that overlap a boost slot go last in the cheapest order, like an
    hour over the cap; the count of heating hours stays."""
    result = priorities.copy()
    in_slot = []
    for t in result.index:
        start = int(_aware(t).timestamp())
        in_slot.append(bool(hb.slots_between(start, start + 3600, boost, boost.slot_s)))
    flags = pd.Series(in_slot, index=result.index)
    result["over_cap"] = (result["over_cap"] | flags) if "over_cap" in result else flags
    logger.info(f"Planner link: {int(flags.sum())} hours in a boost slot go last")
    return result


def link_priorities(priorities: pd.DataFrame, section: dict) -> pd.DataFrame:
    if "planner_link" not in section:
        raise ValueError("config.yaml hot_water section lacks planner_link")
    if not bool(section["planner_link"]):
        return priorities
    return slot_last(priorities, boost_settings(section))


def _given_back(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a mapping")
    return data


def lost_minutes(
    section: dict,
    program_date: str,
    cycles: Callable[[int], list],
    now: int,
    path: Path = GIVEN_BACK,
) -> int:
    """ON minutes lost to hot water in the last 24 h, not given back to an
    earlier program. cycles(since) gives (start, lost_on_min) pairs. A second
    run for the same program date gives back the same cycles again."""
    if not bool(section["planner_link"]):
        return 0
    given = _given_back(path)
    earlier = {s for day, starts in given.items() if day != program_date for s in starts}
    mine = [(s, m) for s, m in cycles(now - LOOKBACK_S) if m > 0 and s not in earlier]
    given[program_date] = sorted(s for s, _ in mine)
    keep = sorted(given)[-KEEP_DAYS:]
    write_json(path, {day: given[day] for day in keep})
    total = sum(m for _, m in mine)
    logger.info(f"Planner link: {total} lost ON minutes given back to {program_date}")
    return total


def _losses(influx, bucket: str) -> Callable[[int], list]:
    def cycles(since: int) -> list:
        start = datetime.datetime.fromtimestamp(since, datetime.timezone.utc)
        flux = f"""
            from(bucket: "{bucket}")
              |> range(start: {start.strftime("%Y-%m-%dT%H:%M:%SZ")})
              |> filter(fn: (r) => r._measurement == "hot_water_cycle"
                   and r.data_type == "actual" and r._field == "lost_on_min")
        """
        return [
            (int(rec.get_time().timestamp()), int(rec.get_value()))
            for table in influx.query_with_retry(flux)
            for rec in table.records
            if rec.get_value() is not None
        ]

    return cycles


def lost_heating_hours(config, influx, program_date: str) -> float:
    """The lost ON minutes as heating hours for the day's program; 0 when the
    planner link is off or the cycle log cannot be read."""
    section = config.hot_water_section
    if not bool(section["planner_link"]):
        return 0.0
    try:
        cycles = _losses(influx, config.influxdb_bucket_load_control)
        return lost_minutes(section, program_date, cycles, int(time.time())) / 60
    except Exception as e:
        logger.error(f"Lost ON minutes not given back: {e}")
        return 0.0
