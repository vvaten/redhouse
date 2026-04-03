# Grafana Dashboard Dynamic Bucket Selection Plan

## Overview

The staging Grafana dashboard should automatically select the optimal InfluxDB
bucket based on the current time window, providing the best resolution without
overloading InfluxDB with raw data scans over long ranges.

## Tier Architecture

| Tier | Bucket | Resolution | Measurement |
|------|--------|-----------|-------------|
| Raw | `checkwatt_staging` | 1 min | `checkwatt` |
| Raw | `shelly_em3_emeters_raw_staging` | 1 min | `shelly_em3` |
| 2 | `emeters_5min_staging` | 5 min | `energy` |
| 3 | `analytics_15min_staging` | 15 min | `analytics` |
| 3 | `analytics_1hour_staging` | 1 hour | `analytics` |

Reference buckets (no switching needed): `spotprice_staging`, `weather_staging`,
`temperatures`, `windpower_staging`, `load_control_staging`.

## Time Window Thresholds

| Window duration | Resolution | Source tier |
|----------------|-----------|-------------|
| <= 48h | 1 min | Raw buckets (checkwatt, shelly_em3, temperatures) |
| <= 8d | 5 min / 15 min | emeters_5min or analytics_15min |
| > 8d | 1 hour | analytics_1hour |

These thresholds apply uniformly across all panels that support tier switching.

## Technique: Conditional Empty Ranges

Flux does not support dynamic `from(bucket:)` variables. The workaround is to
query all tiers but give inactive tiers a zero-width range (`start == stop`)
so they return no data, then `union()` the results.

```flux
windowDuration = int(v: v.timeRangeStop) - int(v: v.timeRangeStart)
oneDayNs = int(v: 24h)
sevenDaysNs = int(v: 7d)

// Only one branch gets a real range; others return empty
e_raw = if windowDuration <= oneDayNs then v.timeRangeStop else v.timeRangeStart
e_5min = if windowDuration > oneDayNs and windowDuration <= sevenDaysNs
         then v.timeRangeStop else v.timeRangeStart
e_15min = if windowDuration > sevenDaysNs then v.timeRangeStop else v.timeRangeStart

union(tables: [
  from(bucket: "raw_bucket") |> range(start: v.timeRangeStart, stop: e_raw) |> ...,
  from(bucket: "5min_bucket") |> range(start: v.timeRangeStart, stop: e_5min) |> ...,
  from(bucket: "15min_bucket") |> range(start: v.timeRangeStart, stop: e_15min) |> ...,
])
```

## Field Mapping Between Tiers

### Power fields (_avg, Watts)

| Concept | checkwatt (Raw) | emeters_5min (Tier 2) | analytics (Tier 3) |
|---------|----------------|-----------------------|--------------------|
| Solar | `SolarYield` | `solar_yield_avg` | `solar_yield_avg` |
| Consumption | -- | `consumption_avg` | `consumption_avg` |
| Net grid | -- | `emeter_avg` | `emeter_avg` |
| Battery charge | `BatteryCharge` | `battery_charge_avg` | `battery_charge_avg` |
| Battery discharge | `BatteryDischarge` | `battery_discharge_avg` | `battery_discharge_avg` |
| Grid import | `EnergyImport` | `energy_import_avg` | `energy_import_avg` |
| Grid export | `EnergyExport` | `energy_export_avg` | `energy_export_avg` |
| Battery SoC | `Battery_SoC` | `Battery_SoC` | `Battery_SoC` |

### Energy fields (Wh) -- naming differs between tiers

| Concept | emeters_5min (Tier 2) | analytics (Tier 3) |
|---------|----------------------|--------------------|
| Solar energy | `solar_yield_diff` | `solar_yield_sum` |
| Consumption | `consumption_diff` | `consumption_sum` |
| Net grid | `emeter_diff` | `emeter_sum` |
| Battery charge | `battery_charge_diff` | `battery_charge_sum` |
| Battery discharge | `battery_discharge_diff` | `battery_discharge_sum` |
| Export | -- | `export_sum` |

### Fields unique to specific tiers

- `phase1_power`, `phase2_power`, `phase3_power`, `total_power` -- raw Shelly EM3 only
- `energy_import_avg`, `energy_export_avg` -- emeters_5min and analytics tiers
- `grid_voltage_avg`, `grid_current_avg`, `grid_power_factor_avg` -- emeters_5min only
- `consumption_max`, `solar_yield_max`, `grid_power_max` -- analytics_1hour only
- Cost/energy flow fields (`net_cost`, `solar_to_consumption`, etc.) -- analytics only

## Implementation Phases

### Phase 1a: Shelly EM3 Phase Power (Panel 24) [DONE]

Per-phase power data only exists in raw tier. No fallback to aggregated data.

- Window <= 48h: query `shelly_em3_emeters_raw_staging`
- Window > 48h: return empty (panel shows "No data")
- Added panel description: "Raw phase data -- zoom to < 48h for data"

### Phase 1b: CheckWatt (Panel 16) [DONE]

3-tier switching with field renaming between raw and aggregated tiers.

- Window <= 48h: `checkwatt_staging` (fields: SolarYield, BatteryCharge, etc.)
- Window <= 8d: `emeters_5min_staging` (fields: solar_yield_avg, etc. -- rename to match)
- Window > 8d: `analytics_15min_staging` (same fields as emeters_5min for the 5 common ones)

Field renaming in aggregated branches via `map()`.

### Phase 0 (prerequisite): Add missing fields to analytics aggregators [DONE]

Added `energy_import_avg` and `energy_export_avg` to `analytics_15min` and
`analytics_1hour` so the import/export breakdown is available at longer ranges.

Files modified:
- `src/aggregation/analytics_15min.py`
- `src/aggregation/analytics_1hour.py`
- `src/aggregation/metric_calculators.py` (added `extract_field` helper)

TODO: Backfill analytics on the Pi for the existing data period.

### Phase 2a: Energy Meters (Panel 14) [DONE]

3-tier switching. Panel title updated from "Energy Meters (5min)" to "Energy Meters".

- Window <= 48h: `emeters_5min_staging` (all 7 fields including import/export)
- Window <= 8d: `analytics_15min_staging` (all 7 fields after Phase 0)
- Window > 8d: `analytics_1hour_staging` (all 7 fields after Phase 0)

Field names are identical across all tiers after Phase 0.
Uses shared `fields` function to avoid duplicating the field filter.

### Phase 2b: Solar & Battery Energy Flow (Panel 20) [DONE]

2-tier switching. Fields identical in both analytics tiers.

- Window <= 8d: `analytics_15min_staging`
- Window > 8d: `analytics_1hour_staging`

Fields: `solar_to_consumption`, `solar_to_battery`, `solar_to_export`,
`battery_to_consumption`, `grid_to_battery`.

### Phase 3: Stat/Gauge Panels (Panels 4-11) [DONE]

2-tier switching. Branches only filter; sum/map applied once after union.

- Window <= 8d: `analytics_15min_staging`
- Window > 8d: `analytics_1hour_staging`

Panels: Produced Electricity, Consumed Electricity, Bought Electricity,
Sold Electricity, Net Cost, Solar Savings, Battery Arbitrage, Total Cost.

Panels 2-3 (Consumption Effect, Avg. Actual Price) stay on `analytics_1hour`
because they join with hourly spot prices.

### Phase 4: Temperature Panels (Panels 15, 17, 19, 21) [DONE]

3-tier switching for temperature panels, 48h guard for humidity.

- Panels 15, 17, 19: 3-tier (raw/15min/1hour) with same field names
- Panel 21 (Humidity): 48h guard only (humidity not in analytics tiers)
- Panel 15 query B (weather forecast) unchanged -- already lightweight

### Panels Not Changed (Reference Data)

These panels query reference buckets with their own natural cadence and
already use `aggregateWindow(v.windowPeriod)` for downsampling:

- Avg. Spot Price (Panel 1) -- `spotprice_staging`
- Spot Price chart (Panel 12) -- `spotprice_staging`
- Weather Forecast (Panel 13) -- `weather_staging`
- Cost per Hour (Panel 18) -- `analytics_1hour_staging` (naturally hourly)
- Wind Power (Panel 22) -- `windpower_staging`
- Heating Schedule (Panel 23) -- `load_control_staging` + `spotprice_staging`
- Cost per Day (Panel 25) -- `analytics_1hour_staging` (aggregated to daily)
