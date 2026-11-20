#!/usr/bin/env python
"""Car tip replay (design 09 step 8): on how many evenings the tip would
mail, and what it would save, on two winters of hourly prices.

Each evening starts from the month's real counted hours up to 16:00, as
the power cap state would hold them. A flat-price pass shows how many
tips come from the power charge alone.

Usage: python scripts/sim/run_car_tip_sim.py
"""

import datetime
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.common.local_time import HELSINKI  # noqa: E402
from src.control import car_tip as ct  # noqa: E402
from src.control.power_cap import CapSettings, factor, month_key  # noqa: E402

DATA = Path("E:/redhouse_data/sim/data")
CAP = CapSettings(0.8, 22, 7, 7.5, 0.3, 0.1, 1.97, 34.5)
SETTINGS = ct.TipSettings(45.0, 5.5, 11.2, 7, 17, 1.0)
# House base and battery allowance; the replay knows no pump plan
OTHER_KW = 3.1
FIRST = datetime.date(2024, 11, 1)
END = datetime.date(2026, 4, 1)


def counted_history(hourly: pd.DataFrame) -> dict:
    """{hour start: counted kW} from the mean import W of each hour."""
    kw = hourly["energy_import_avg"].dropna() / 1000.0
    return {t.to_pydatetime(): float(v) * factor(t.to_pydatetime(), CAP) for t, v in kw.items()}


def replay(prices: dict, counted: dict) -> pd.DataFrame:
    rows = []
    day = FIRST
    while day < END:
        now = HELSINKI.localize(datetime.datetime.combine(day, datetime.time(16, 35)))
        now_utc = now.astimezone(datetime.timezone.utc)
        hours = [now_utc.replace(minute=0) + datetime.timedelta(hours=i) for i in range(26)]
        month = month_key(now_utc)
        so_far = {t: kw for t, kw in counted.items() if month_key(t) == month and t < hours[0]}
        ctx = {"other_kw": {t: OTHER_KW for t in hours}, "month_counted": so_far}
        result, _ = ct.tip(prices, now_utc, SETTINGS, CAP, ctx)
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
        day += datetime.timedelta(days=1)
    return pd.DataFrame(rows, columns=["day", "start", "saving_eur", "energy_eur", "charge_eur"])


def _summary(name: str, table: pd.DataFrame) -> list:
    if table.empty:
        return [f"{name}: no tips."]
    return [
        f"{name}: tips mailed {len(table)}; mean saving {table['saving_eur'].mean():.2f} EUR "
        f"(energy {table['energy_eur'].mean():.2f}, power charge "
        f"{table['charge_eur'].mean():.2f}); median {table['saving_eur'].median():.2f}.",
        f"{name}: tips with no energy saving {int((table['energy_eur'] <= 0).sum())}.",
    ]


def main():
    hourly = pd.read_csv(DATA / "hourly.csv.gz", index_col=0)
    hourly.index = pd.to_datetime(hourly.index, utc=True, format="ISO8601")
    prices = {t.to_pydatetime(): float(v) for t, v in hourly["price_total"].dropna().items()}
    counted = counted_history(hourly)
    table = replay(prices, counted)
    flat = replay({t: 0.15 for t in prices}, counted)
    out = Path(f"E:/redhouse_data/sim/car_tip_{datetime.date.today():%Y%m%d}")
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "tips.csv", index=False)
    by_month = table.groupby(pd.to_datetime(table["day"]).dt.strftime("%Y-%m")).size()
    lines = [
        f"# Car tip replay {datetime.date.today()}",
        "",
        f"Evenings replayed: {(END - FIRST).days}. Plug-in {SETTINGS.plug_in_hour}:00, "
        f"threshold {SETTINGS.min_saving_eur} EUR.",
        *_summary("Real prices", table),
        *_summary("Flat prices", flat),
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
    print("\n".join(lines[:8]))


if __name__ == "__main__":
    main()
