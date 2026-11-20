"""Power budget per hour for the program generator.

An hour where the pump's ON load would not fit under the planned cap
goes to the end of the cheapest-hours order. The count of heating hours
stays the same: an over-cap hour is used only when the day needs it.
"""

import datetime
from dataclasses import dataclass, fields
from pathlib import Path

import pandas as pd

from src.common.local_time import HELSINKI
from src.common.logger import setup_logger
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


def load_budget_settings(section: dict) -> BudgetSettings:
    names = [f.name for f in fields(BudgetSettings)]
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
    )


def fixed_kw(hour_start: datetime.datetime, settings: BudgetSettings) -> float:
    """The load the plan cannot move in the hour: base, battery, sauna reserve."""
    local = hour_start.astimezone(HELSINKI)
    sauna = (
        local.weekday() in settings.sauna_weekdays
        and settings.sauna_start_hour <= local.hour < settings.sauna_end_hour
    )
    reserve = settings.sauna_kw * settings.sauna_share if sauna else 0.0
    return settings.base_kw + settings.battery_allowance_kw + reserve


def headroom_kw(
    hour_start: datetime.datetime, limit_kw: float, cap: CapSettings, settings: BudgetSettings
) -> float:
    """What the movable loads may add in the hour: the limit over the
    hour's counting factor, less the fixed load."""
    return limit_kw / factor(hour_start, cap) - fixed_kw(hour_start, settings)


def budget_priorities(
    priorities: pd.DataFrame, config, state_file: Path, program_date: datetime.datetime
) -> pd.DataFrame:
    """The priorities with the budget applied, or unchanged when it is off.
    The cap is the one the day starts with: a new month starts at its floor."""
    settings = load_budget_settings(config.load_budget_section)
    if not settings.enabled:
        return priorities
    cap = load_cap_settings(config.power_cap_section)
    day_start = HELSINKI.localize(datetime.datetime.combine(program_date.date(), datetime.time()))
    if not state_file.exists():
        logger.warning(f"No power cap state at {state_file}: the budget uses the floor")
    limit = plan_limit_kw(read_cap_kw(state_file, day_start, cap), cap)
    result = apply_budget(priorities, limit, cap, settings)
    logger.info(
        f"Power budget: plan limit {limit:.2f} kW, {int(result['over_cap'].sum())} hours over"
    )
    return result


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
