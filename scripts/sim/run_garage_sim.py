#!/usr/bin/env python
"""Garage simulation 4.2 of design 09: hindcast, policy runs, fault runs.

Reads E:/redhouse_data/sim/data and writes report.md plus CSVs to
E:/redhouse_data/sim/garage_<date>/.

Usage: python scripts/sim/run_garage_sim.py [--winters 2023 2024 2025] [--quick]
"""

import argparse
import dataclasses
import datetime
import json
import random
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.control.garage_model import GarageParams  # noqa: E402
from src.simulation import garage_sim as gs  # noqa: E402

DATA = Path("E:/redhouse_data/sim/data")
HEATER_KW = 2.13
CONTROLLER = GarageParams(
    target_c=5.0,
    floor_c=2.0,
    stale_after_s=10800,
    loss_k_per_h_per_k=0.010,
    loss_k_per_h=0.08,
    heat_gross_k_per_h=0.6,
)
# Controller loss intercepts tried in the cold-regime hindcast
B_CANDIDATES = [0.06, 0.08, 0.10, 0.12, 0.14]


def load(name):
    df = pd.read_csv(DATA / f"{name}.csv.gz", index_col=0)
    df.index = pd.to_datetime(df.index, utc=True, format="ISO8601")
    return df


def policy_runs(quarter, plant_fit, years, out):
    rng = random.Random(7)
    rows = []
    worst = gs.Plant(CONTROLLER.loss_k_per_h_per_k * 1.3, CONTROLLER.loss_k_per_h * 1.3, 0.55)
    for year in years:
        part = gs.winter(quarter, year)
        # Not fitted: the gross heater rate is drawn per winter
        heat = rng.uniform(0.55, 0.9)
        plant = gs.Plant(plant_fit[0], plant_fit[1], heat)
        cases = {
            "a_thermostat_6c": (gs.thermostat_policy(6.0), plant, 0.0),
            "b_model_perfect": (gs.model_policy(CONTROLLER, HEATER_KW), plant, 0.0),
            "c_model_warm_bias_2k": (gs.model_policy(CONTROLLER, HEATER_KW), plant, 2.0),
            "d_relays_off": (gs.off_policy(), plant, 0.0),
            "e_worst_plant_warm_bias": (gs.model_policy(CONTROLLER, HEATER_KW), worst, 2.0),
        }
        for name, (policy, pl, bias) in cases.items():
            run = gs.run_policy(part, policy, pl, start_c=6.0, bias_k=bias, seed=year)
            m = gs.metrics(run, HEATER_KW)
            m.update(
                {
                    "winter": f"{year}-{year + 1 - 2000}",
                    "policy": name,
                    "plant_heat": round(heat, 2),
                }
            )
            rows.append(m)
            run.to_csv(out / f"run_{year}_{name}.csv.gz", compression="gzip")
            print(
                f"{year} {name}: {json.dumps({k: round(v, 2) if isinstance(v, float) else v for k, v in m.items()})}",
                flush=True,
            )
    return pd.DataFrame(rows)


def tune_b(hourly):
    rows = []
    for b in B_CANDIDATES:
        hind = gs.hindcast(hourly, dataclasses.replace(CONTROLLER, loss_k_per_h=b))
        rows.append(
            {
                "b": b,
                "windows": len(hind),
                "model_at_or_below": float((hind["predicted"] <= hind["actual"] + 1e-9).mean()),
                "mean_error_k": float(hind["error"].mean()),
                "mae_k": float(hind["error"].abs().mean()),
            }
        )
    return pd.DataFrame(rows).round(3)


def fault_runs(quarter, plant_fit):
    """A reading that stops for 6 h in a cold week must turn the heat on at 3 h."""
    part = gs.winter(quarter, 2025).iloc[: 96 * 7]
    plant = gs.Plant(plant_fit[0], plant_fit[1], 0.5)
    run = gs.run_policy(
        part, gs.model_policy(CONTROLLER, HEATER_KW), plant, 4.0, stale_slots=range(96, 120)
    )
    stale = run.iloc[96:121]
    first_stale_on = stale.index[(stale["reason"] == "stale_reading")]
    hours_to_on = (
        (first_stale_on[0] - stale.index[0]).total_seconds() / 3600 if len(first_stale_on) else None
    )
    return {"stale_6h_heat_on_after_h": hours_to_on, "min_c": float(run["garage_c"].min())}


def write_report(out, fit, hind, tune, table, faults):
    lines = [f"# Garage simulation {datetime.date.today()}", ""]
    lines += [
        f"Plant cooling fit (median, coasting hours): a={fit[0]:.4f} K/h per K, b={fit[1]:.3f} K/h, n={fit[2]} h.",
        f"Controller model (pessimistic): a={CONTROLLER.loss_k_per_h_per_k}, "
        f"b={CONTROLLER.loss_k_per_h}, gross heat {CONTROLLER.heat_gross_k_per_h} K/h.",
        "",
        "## Hindcast, 12 h coasting windows starting under 8 C",
        "",
    ]
    if hind.empty:
        lines.append("No windows found.")
    else:
        share = float((hind["predicted"] <= hind["actual"] + 1e-9).mean())
        lines += [
            f"Windows: {len(hind)}. Model at or below the measured end: {share:.0%} (gate 90%).",
            f"Mean error {hind['error'].mean():.2f} K, mean absolute {hind['error'].abs().mean():.2f} K (gate under 1 K).",
        ]
    lines += [
        "",
        "Controller loss intercept b against the hindcast (a fixed):",
        "```",
        tune.to_string(index=False),
        "```",
    ]
    lines += ["", "## Policy runs", "", "```", table.round(2).to_string(index=False), "```", ""]
    lines += ["## Fault runs", "", json.dumps(faults), ""]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--winters", nargs="*", type=int, default=[2023, 2024, 2025])
    args = parser.parse_args()
    out = Path(f"E:/redhouse_data/sim/garage_{datetime.date.today():%Y%m%d}")
    out.mkdir(parents=True, exist_ok=True)
    hourly = load("hourly")
    quarter = load("quarter")
    fit = gs.fit_cooling(hourly)
    print(f"cooling fit {fit}", flush=True)
    hind = gs.hindcast(hourly, CONTROLLER)
    tune = tune_b(hourly)
    print(tune.to_string(index=False), flush=True)
    hind.to_csv(out / "hindcast.csv")
    print(f"hindcast windows {len(hind)}", flush=True)
    table = policy_runs(quarter, fit, args.winters, out)
    table.to_csv(out / "policies.csv", index=False)
    faults = fault_runs(quarter, fit)
    print(f"faults {faults}", flush=True)
    write_report(out, fit, hind, tune, table, faults)
    print(f"report: {out / 'report.md'}")


if __name__ == "__main__":
    main()
