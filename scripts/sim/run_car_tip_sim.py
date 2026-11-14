#!/usr/bin/env python
"""Car tip replay (design 09 step 8): on how many evenings the tip would
mail, and what it would save, on two winters of hourly prices.

Usage: python scripts/sim/run_car_tip_sim.py
"""

import datetime
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.common.local_time import HELSINKI  # noqa: E402
from src.control import car_tip as ct  # noqa: E402
from src.control.power_cap import CapSettings  # noqa: E402

DATA = Path("E:/redhouse_data/sim/data")
CAP = CapSettings(0.8, 22, 7, 7.5, 0.3, 0.1, 1.97, 34.5)
SETTINGS = ct.TipSettings(45.0, 5.5, 11.2, 7, 22, 1.0)
# House base and battery allowance; the replay knows no pump plan
OTHER_KW = 3.1


def main():
    hourly = pd.read_csv(DATA / "hourly.csv.gz", index_col=0)
    hourly.index = pd.to_datetime(hourly.index, utc=True, format="ISO8601")
    prices = {t.to_pydatetime(): float(v) for t, v in hourly["price_total"].dropna().items()}
    rows = []
    day = datetime.date(2024, 11, 1)
    while day < datetime.date(2026, 4, 1):
        now = HELSINKI.localize(datetime.datetime.combine(day, datetime.time(16, 35)))
        now_utc = now.astimezone(datetime.timezone.utc)
        hours = [now_utc.replace(minute=0) + datetime.timedelta(hours=i) for i in range(26)]
        ctx = {"other_kw": {t: OTHER_KW for t in hours}, "month_counted": {}}
        result, _ = ct.tip(prices, now_utc, SETTINGS, CAP, ctx)
        if result is not None:
            low, habit, _ = result
            rows.append(
                {
                    "day": day,
                    "start": ct.local_label(low.start),
                    "saving_eur": round(habit.total_eur - low.total_eur, 2),
                }
            )
        day += datetime.timedelta(days=1)
    table = pd.DataFrame(rows, columns=["day", "start", "saving_eur"])
    out = Path(f"E:/redhouse_data/sim/car_tip_{datetime.date.today():%Y%m%d}")
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "tips.csv", index=False)
    days = (datetime.date(2026, 4, 1) - datetime.date(2024, 11, 1)).days
    lines = [
        f"# Car tip replay {datetime.date.today()}",
        "",
        f"Evenings replayed: {days}. Tips mailed: {len(table)}.",
        f"Mean saving per tip: {table['saving_eur'].mean():.2f} EUR; "
        f"total if every tip was followed: {table['saving_eur'].sum():.2f} EUR.",
        "",
        "```",
        table.head(30).to_string(index=False),
        "```",
    ]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:5]))


if __name__ == "__main__":
    main()
