"""Power budget per hour for the program generator.

An hour where the pump's ON load would not fit under the planned cap
goes to the end of the cheapest-hours order. The count of heating hours
stays the same: an over-cap hour is used only when the day needs it.
"""

import datetime
import math
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Optional

import pandas as pd

from src.common.local_time import HELSINKI
from src.common.logger import setup_logger
from src.control import hot_water_boost as hb
from src.control.hot_water_planner import (
    BoostBudget,
    boost_budget,
    boost_settings,
    boost_share_kw,
    link_priorities,
)
from src.control.power_cap import (
    CapSettings,
    factor,
    load_cap_settings,
    plan_limit_kw,
    read_cap_kw,
)

logger = setup_logger(__name__)


@dataclass(frozen=True)
class BudgetSettings:
    enabled: bool
    base_kw: float
    battery_allowance_kw: float
    pump_on_kw: float
    sauna_kw: float
    # The share of an hour a sauna session runs (0.5 for :30 to :30)
    sauna_share: float
    # Python weekday numbers (Monday 0) and local hours [start, end)
    sauna_weekdays: tuple
    sauna_start_hour: int
    sauna_end_hour: int
    # The hot water boost's heater share, when hot_water.boost_budget is on
    boost: Optional[BoostBudget] = None


def load_budget_settings(section: dict, hot_water: Optional[dict] = None) -> BudgetSettings:
    """From the load_budget section; with the hot_water section, the boost share too."""
    names = [f.name for f in fields(BudgetSettings) if f.name != "boost"]
    missing = [k for k in names if k not in section]
    if missing:
        raise ValueError(f"config.yaml load_budget section lacks {missing}")
    return BudgetSettings(
        enabled=bool(section["enabled"]),
        base_kw=float(section["base_kw"]),
        battery_allowance_kw=float(section["battery_allowance_kw"]),
        pump_on_kw=float(section["pump_on_kw"]),
        sauna_kw=float(section["sauna_kw"]),
        sauna_share=float(section["sauna_share"]),
        sauna_weekdays=tuple(int(d) for d in section["sauna_weekdays"]),
        sauna_start_hour=int(section["sauna_start_hour"]),
        sauna_end_hour=int(section["sauna_end_hour"]),
        boost=None if hot_water is None else boost_budget(hot_water),
    )


def fixed_kw(hour_start: datetime.datetime, settings: BudgetSettings) -> float:
    """The load the plan cannot move in the hour: base, battery, sauna
    reserve and the share of a hot water boost."""
    local = hour_start.astimezone(HELSINKI)
    sauna = (
        local.weekday() in settings.sauna_weekdays
        and settings.sauna_start_hour <= local.hour < settings.sauna_end_hour
    )
    reserve = settings.sauna_kw * settings.sauna_share if sauna else 0.0
    boost = boost_share_kw(hour_start, settings.boost)
    return settings.base_kw + settings.battery_allowance_kw + reserve + boost


def headroom_kw(
    hour_start: datetime.datetime, limit_kw: float, cap: CapSettings, settings: BudgetSettings
) -> float:
    """What the movable loads may add in the hour: the limit over the
    hour's counting factor, less the fixed load."""
    return limit_kw / factor(hour_start, cap) - fixed_kw(hour_start, settings)


def budget_priorities(
    priorities: pd.DataFrame, config, state_file: Path, program_date: datetime.datetime
) -> pd.DataFrame:
    """The priorities with the budget and the hot water planner link applied;
    unchanged when both are off. The cap is the one the day starts with: a
    new month starts at its floor."""
    hot_water = config.hot_water_section
    settings = load_budget_settings(config.load_budget_section, hot_water)
    if not settings.enabled:
        return link_priorities(priorities, hot_water)
    cap = load_cap_settings(config.power_cap_section)
    day_start = HELSINKI.localize(datetime.datetime.combine(program_date.date(), datetime.time()))
    if not state_file.exists():
        logger.warning(f"No power cap state at {state_file}: the budget uses the floor")
    limit = plan_limit_kw(read_cap_kw(state_file, day_start, cap), cap)
    result = apply_budget(priorities, limit, cap, settings)
    logger.info(
        f"Power budget: plan limit {limit:.2f} kW, {int(result['over_cap'].sum())} hours over"
    )
    return link_priorities(result, hot_water)


def quarters_that_fit(headroom: float, pump_on_kw: float) -> int:
    """ON quarters of the pump whose hourly mean stays within the headroom."""
    return max(0, min(4, math.floor(4 * headroom / pump_on_kw + 1e-9)))


def _day_start(t) -> datetime.datetime:
    local = _aware(t).astimezone(HELSINKI)
    return HELSINKI.localize(datetime.datetime.combine(local.date(), datetime.time()))


def _budget_caps(hours: list, config, state_file: Path, settings: BudgetSettings) -> dict:
    cap = load_cap_settings(config.power_cap_section)
    if not state_file.exists():
        logger.warning(f"No power cap state at {state_file}: the budget uses the floor")
    limits: dict = {}
    caps = {}
    for h in hours:
        day = _day_start(h)
        if day not in limits:
            limits[day] = plan_limit_kw(read_cap_kw(state_file, day, cap), cap)
        room = headroom_kw(_aware(h), limits[day], cap, settings)
        caps[h] = quarters_that_fit(room, settings.pump_on_kw)
    return caps


def hour_caps(hours: list, config, state_file: Path) -> dict:
    """{hour start: ON quarters allowed} for aware hour starts: 4 without a
    budget, 0 in a boost slot with the planner link. Each hour uses the cap
    its own local day starts with."""
    hot_water = config.hot_water_section
    settings = load_budget_settings(config.load_budget_section, hot_water)
    caps = _budget_caps(hours, config, state_file, settings) if settings.enabled else {}
    caps = {h: caps[h] if h in caps else 4 for h in hours}
    if "planner_link" not in hot_water:
        raise ValueError("config.yaml hot_water section lacks planner_link")
    if bool(hot_water["planner_link"]):
        boost = boost_settings(hot_water)
        for h in hours:
            start = int(_aware(h).timestamp())
            if hb.slots_between(start, start + 3600, boost, boost.slot_s):
                caps[h] = 0
    limited = sum(1 for k in caps.values() if k < 4)
    logger.info(f"Hour caps: {limited} of {len(caps)} hours take fewer than 4 ON quarters")
    return caps


def _aware(t) -> datetime.datetime:
    stamp = pd.Timestamp(t)
    if stamp.tzinfo is None:
        raise ValueError(f"The priorities index must carry its zone: {t}")
    return stamp.to_pydatetime()


def apply_budget(
    priorities: pd.DataFrame, limit_kw: float, cap: CapSettings, settings: BudgetSettings
) -> pd.DataFrame:
    """The priorities with an over_cap column: True where the pump's ON load
    would not fit. The hour selection takes those last; prices stay as they are."""
    result = priorities.copy()
    result["over_cap"] = [
        settings.pump_on_kw > headroom_kw(_aware(t), limit_kw, cap, settings) for t in result.index
    ]
    return result
