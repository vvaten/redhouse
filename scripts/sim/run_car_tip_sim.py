#!/usr/bin/env python
"""Car tip replay (design 09 step 8): on how many evenings the tip would
mail, and what it would save, on the spot prices of two winters.

Each evening starts from the month's real counted hours up to 16:00, as
the power cap state would hold them. A flat-price pass shows how many
tips come from the power charge alone. Since quarter prices began, a
second pass compares quarter starts on quarter prices with whole-hour
starts on hour-mean prices, both costed at the real quarter prices.

Usage: python scripts/sim/run_car_tip_sim.py
"""

import datetime
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.aggregation.quarter_prices import QUARTER_PRICES_FROM  # noqa: E402
from src.common.local_time import HELSINKI  # noqa: E402
from src.control import car_tip as ct  # noqa: E402
from src.control.power_cap import CapSettings, factor, month_key  # noqa: E402

DATA = Path("E:/redhouse_data/sim/data")
CAP = CapSettings(0.8, 22, 7, 7.5, 0.3, 0.1, 1.97, 34.5)
SETTINGS = ct.TipSettings(45.0, 5.5, 11.2, 7, 17, 1.0)
# House base and battery allowance; the replay knows no pump plan
OTHER_KW = 3.1
FIRST = datetime.date(2024, 11, 1)
QUARTERS_FROM = datetime.date(2025, 10, 1)
END = datetime.date(2026, 11, 14)


def counted_history(hourly: pd.DataFrame) -> dict:
    """{hour start: counted kW} from the mean import W of each hour."""
    kw = hourly["energy_import_avg"].dropna() / 1000.0
    return {t.to_pydatetime(): float(v) * factor(t.to_pydatetime(), CAP) for t, v in kw.items()}


def quarter_series(spot: pd.DataFrame) -> pd.Series:
    """price_total per quarter; an hourly price covers its four quarters.
    A quarter missing after quarter prices began stays missing, as live."""
    prices = spot["price_total"].dropna()
    hourly = prices[prices.index < QUARTER_PRICES_FROM].resample("15min").ffill(limit=3)
    return pd.concat([hourly, prices[prices.index >= QUARTER_PRICES_FROM]]).dropna()


def hour_mean(quarters: pd.Series) -> pd.Series:
    """Each quarter at the mean price of its hour, as the old tip saw it."""
    return quarters.groupby(quarters.index.floor("h")).transform("mean")


def as_dict(series: pd.Series) -> dict:
    return {t.to_pydatetime(): float(v) for t, v in series.items()}


def evening(day: datetime.date, counted: dict) -> tuple:
    now = HELSINKI.localize(datetime.datetime.combine(day, datetime.time(16, 35)))
    now_utc = now.astimezone(datetime.timezone.utc)
    hours = [now_utc.replace(minute=0) + datetime.timedelta(hours=i) for i in range(26)]
    month = month_key(now_utc)
    so_far = {t: kw for t, kw in counted.items() if month_key(t) == month and t < hours[0]}
    return now_utc, {"other_kw": {t: OTHER_KW for t in hours}, "month_counted": so_far}


def days(first: datetime.date, end: datetime.date):
    day = first
    while day < end:
        yield day
        day += datetime.timedelta(days=1)


def replay(prices: dict, counted: dict) -> pd.DataFrame:
    rows = []
    for day in days(FIRST, END):
        now, ctx = evening(day, counted)
        result, _ = ct.tip(prices, now, SETTINGS, CAP, ctx)
        if result is not None:
            low, plug_in, _ = result
            rows.append(
                {
                    "day": day,
                    "start": ct.local_label(low.start),
                    "saving_eur": round(plug_in.total_eur - low.total_eur, 2),
                    "energy_eur": round(plug_in.energy_eur - low.energy_eur, 2),
                    "charge_eur": round(plug_in.charge_rise_eur - low.charge_rise_eur, 2),
                }
            )
    return pd.DataFrame(rows, columns=["day", "start", "saving_eur", "energy_eur", "charge_eur"])


def cheapest(prices: dict, now, ctx: dict, whole_hours: bool):
    starts = [
        t for t in ct._starts(now, SETTINGS.kw_low, SETTINGS) if t.minute == 0 or not whole_hours
    ]
    windows = [ct.window_at(prices, t, SETTINGS.kw_low, SETTINGS, CAP, ctx) for t in starts]
    priced = [w for w in windows if w is not None]
    return min(priced, key=lambda w: (w.total_eur, w.start)) if priced else None


def compare(real: dict, mean: dict, counted: dict) -> pd.DataFrame:
    """Per evening: the plug-in, the old pick and the new pick at real prices."""
    rows = []
    for day in days(QUARTERS_FROM, END):
        now, ctx = evening(day, counted)
        new = cheapest(real, now, ctx, whole_hours=False)
        old_pick = cheapest(mean, now, ctx, whole_hours=True)
        plug_in = ct.plug_in_window(real, now, SETTINGS.kw_low, SETTINGS, CAP, ctx)
        if new is None or old_pick is None or plug_in is None:
            continue
        old = ct.window_at(real, old_pick.start, SETTINGS.kw_low, SETTINGS, CAP, ctx)
        rows.append(
            {
                "day": day,
                "new_start": ct.local_label(new.start),
                "old_start": ct.local_label(old.start),
                "plug_in_eur": round(plug_in.total_eur, 3),
                "old_eur": round(old.total_eur, 3),
                "new_eur": round(new.total_eur, 3),
            }
        )
    return pd.DataFrame(rows)


def _summary(name: str, table: pd.DataFrame) -> list:
    if table.empty:
        return [f"{name}: no tips."]
    return [
        f"{name}: tips mailed {len(table)}; mean saving {table['saving_eur'].mean():.2f} EUR "
        f"(energy {table['energy_eur'].mean():.2f}, power charge "
        f"{table['charge_eur'].mean():.2f}); median {table['saving_eur'].median():.2f}.",
        f"{name}: tips with no energy saving {int((table['energy_eur'] <= 0).sum())}.",
    ]


def _comparison(table: pd.DataFrame) -> list:
    gain = table["old_eur"] - table["new_eur"]
    moved = table["new_start"] != table["old_start"]
    return [
        f"Quarter era {QUARTERS_FROM} to {END}: {len(table)} evenings.",
        f"Mean session cost: plug-in {table['plug_in_eur'].mean():.2f}, whole-hour pick "
        f"{table['old_eur'].mean():.2f}, quarter pick {table['new_eur'].mean():.2f} EUR.",
        f"Quarter pick cheaper by {gain.mean():.3f} EUR a session (median {gain.median():.3f}, "
        f"max {gain.max():.2f}); start moved on {int(moved.sum())} evenings; "
        f"dearer on {int((gain < -1e-9).sum())}.",
    ]


def main():
    hourly = pd.read_csv(DATA / "hourly.csv.gz", index_col=0)
    hourly.index = pd.to_datetime(hourly.index, utc=True, format="ISO8601")
    spot = pd.read_csv(DATA / "spot.csv.gz", index_col=0)
    spot.index = pd.to_datetime(spot.index, utc=True, format="ISO8601")
    quarters = quarter_series(spot)
    prices = as_dict(quarters)
    counted = counted_history(hourly)
    table = replay(prices, counted)
    flat = replay({t: 0.15 for t in prices}, counted)
    versus = compare(prices, as_dict(hour_mean(quarters)), counted)
    out = Path(f"E:/redhouse_data/sim/car_tip_{datetime.date.today():%Y%m%d}")
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "tips.csv", index=False)
    versus.to_csv(out / "quarter_vs_hour.csv", index=False)
    by_month = table.groupby(pd.to_datetime(table["day"]).dt.strftime("%Y-%m")).size()
    lines = [
        f"# Car tip replay {datetime.date.today()}",
        "",
        f"Evenings replayed: {(END - FIRST).days}. Plug-in {SETTINGS.plug_in_hour}:00, "
        f"threshold {SETTINGS.min_saving_eur} EUR.",
        *_summary("Real prices", table),
        *_summary("Flat prices", flat),
        *_comparison(versus),
        "",
        "Tips per month:",
        "```",
        by_month.to_string(),
        "```",
        "",
        "```",
        table.head(30).to_string(index=False),
        "```",
    ]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:10]))


if __name__ == "__main__":
    main()
