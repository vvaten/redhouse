#!/usr/bin/env python
"""Power cap simulation 4.3 run 1 of design 09: the month's third-highest
counted hour from analytics_1hour emeter_avg, against Helen's invoices.

Usage: python scripts/sim/run_cap_sim.py
"""

import datetime
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.control import power_cap as pc  # noqa: E402

DATA = Path("E:/redhouse_data/sim/data")
SETTINGS = pc.CapSettings(
    night_factor=0.8,
    night_start_hour=22,
    night_end_hour=7,
    month_floor_kw=0.0,
    plan_margin_kw=0.3,
    guard_margin_kw=0.1,
    tariff_eur_per_kw=1.97,
    max_import_kw=34.5,
)
# Billed hour (local start) and kW from the owner's invoices
INVOICES = {
    "2026-08": ("2026-08-05 15:00", 10.99),
    "2026-09": ("2026-09-12 21:00", 7.94),
    "2026-10": ("2026-10-13 22:00", 8.70),
}


def raw_path_check(hourly):
    """Hourly import from the 3EM minute counter, the way the live update
    computes it, against analytics emeter_avg for the same hours."""
    em3 = pd.read_csv(DATA / "em3_minute.csv.gz", index_col=0)
    em3.index = pd.to_datetime(em3.index, utc=True, format="ISO8601")
    first = em3["net_total_energy"].dropna().groupby(em3.index.floor("h")).first()
    raw = pc.hourly_import_kw({t.to_pydatetime(): float(v) for t, v in first.items()}, SETTINGS)
    rows = []
    for t, kw in raw.items():
        ts = pd.Timestamp(t)
        if ts in hourly.index and pd.notna(hourly.loc[ts, "emeter_avg"]):
            ref = max(float(hourly.loc[ts, "emeter_avg"]), 0.0) / 1000
            rows.append({"hour": ts, "raw_kw": kw, "analytics_kw": ref, "diff_kw": kw - ref})
    return pd.DataFrame(rows)


def main():
    hourly = pd.read_csv(DATA / "hourly.csv.gz", index_col=0)
    hourly.index = pd.to_datetime(hourly.index, utc=True, format="ISO8601")
    import_kw = {
        t.to_pydatetime(): max(float(v), 0.0) / 1000
        for t, v in hourly["emeter_avg"].dropna().items()
    }
    counted = pc.counted_hours(import_kw, SETTINGS)
    by_month: dict = {}
    for t, kw in counted.items():
        by_month.setdefault(pc.month_key(t), {})[t] = kw
    out = Path(f"E:/redhouse_data/sim/cap_{datetime.date.today():%Y%m%d}")
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for month in sorted(by_month):
        cap, top = pc.month_cap(by_month[month], SETTINGS)
        third_hour = (
            top[2][0].astimezone(pc.HELSINKI).strftime("%Y-%m-%d %H:%M") if len(top) == 3 else ""
        )
        row = {
            "month": month,
            "hours": len(by_month[month]),
            "cap_kw": round(cap, 2),
            "third_hour": third_hour,
        }
        if month in INVOICES:
            billed_hour, billed_kw = INVOICES[month]
            row.update(
                billed_hour=billed_hour,
                billed_kw=billed_kw,
                diff_kw=round(cap - billed_kw, 2),
                hour_match=third_hour == billed_hour,
                gate=abs(cap - billed_kw) <= 0.15 and third_hour == billed_hour,
            )
        rows.append(row)
    table = pd.DataFrame(rows)
    table.to_csv(out / "months.csv", index=False)
    checked = table.dropna(subset=["billed_kw"]) if "billed_kw" in table else table.iloc[0:0]
    lines = [
        f"# Power cap replay {datetime.date.today()}",
        "",
        "Source: analytics_1hour emeter_avg (3EM net), night 22-07 x 0.8, hours by start.",
        "",
        "## Against the invoices",
        "",
        "```",
        checked.to_string(index=False),
        "```",
        "",
        f"Gate (hour equal, within 0.15 kW): {'PASS' if bool(checked['gate'].all()) else 'FAIL'}",
        "",
        "## Every month",
        "",
        "```",
        table[["month", "hours", "cap_kw", "third_hour"]].to_string(index=False),
        "```",
    ]
    check = raw_path_check(hourly)
    check.to_csv(out / "raw_path_check.csv", index=False)
    big = check[check["diff_kw"].abs() > 0.3]
    lines += [
        "",
        "## Live path (3EM minute counter) against analytics emeter_avg",
        "",
        f"Hours compared: {len(check)}, mean diff {check['diff_kw'].mean():.3f} kW, "
        f"mean abs {check['diff_kw'].abs().mean():.3f} kW, "
        f"max abs {check['diff_kw'].abs().max():.3f} kW.",
        f"Hours over 0.3 kW apart: {len(big)}.",
        "```",
        big.head(20).to_string(index=False),
        "```",
    ]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[4:12]))


if __name__ == "__main__":
    main()
