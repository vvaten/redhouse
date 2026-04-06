# Grafana Dashboard Dynamic Bucket Selection Plan

## Status: IMPLEMENTED

All phases complete. Dashboard deployed to staging Grafana.

## Overview

The staging Grafana dashboard automatically selects the optimal InfluxDB
bucket based on the current time window, providing the best resolution without
overloading InfluxDB with raw data scans over long ranges.

## Tier Architecture

| Tier | Bucket | Resolution | Measurement |
|------|--------|-----------|-------------|
| Raw | `checkwatt_staging` | 1 min | `checkwatt` |
| Raw | `shelly_em3_emeters_raw_staging` | 1 min | `shelly_em3` |
| Raw | `temperatures` | 1 min | `temperatures`, `humidities` |
| 2 | `emeters_5min_staging` | 5 min | `energy` |
| 3 | `analytics_15min_staging` | 15 min | `analytics` |
| 3 | `analytics_1hour_staging` | 1 hour | `analytics` |

Reference buckets (no switching needed): `spotprice_staging`, `weather_staging`,
`windpower_staging`, `load_control_staging`.

## Time Window Thresholds

| Window duration | Resolution | Source tier |
|----------------|-----------|-------------|
| <= 48h | 1 min | Raw buckets (checkwatt, shelly_em3, temperatures) |
| <= 8d | 5 min / 15 min | emeters_5min or analytics_15min |
| > 8d | 1 hour | analytics_1hour |

These thresholds apply uniformly across all panels that support tier switching.

## Technique: Epoch Fallback Ranges

Flux does not support dynamic `from(bucket:)` variables, and InfluxDB rejects
zero-width ranges (`start == stop`). The workaround: query all tiers but give
inactive tiers a 1-second range in 1970 (valid range, guaranteed no data),
then `union()` the results.

```flux
windowDuration = int(v: v.timeRangeStop) - int(v: v.timeRangeStart)
twoDaysNs = int(v: 48h)
eighDaysNs = int(v: 8d)

// Active tier gets real range; inactive tiers get epoch fallback
rawStart = if windowDuration <= twoDaysNs then v.timeRangeStart else 1970-01-01T00:00:00Z
rawStop = if windowDuration <= twoDaysNs then v.timeRangeStop else 1970-01-01T00:00:01Z
t2Start = if windowDuration > twoDaysNs and windowDuration <= eighDaysNs
          then v.timeRangeStart else 1970-01-01T00:00:00Z
t2Stop = if windowDuration > twoDaysNs and windowDuration <= eighDaysNs
         then v.timeRangeStop else 1970-01-01T00:00:01Z
t3Start = if windowDuration > eighDaysNs then v.timeRangeStart else 1970-01-01T00:00:00Z
t3Stop = if windowDuration > eighDaysNs then v.timeRangeStop else 1970-01-01T00:00:01Z

union(tables: [
  from(bucket: "raw_bucket") |> range(start: rawStart, stop: rawStop) |> ...,
  from(bucket: "5min_bucket") |> range(start: t2Start, stop: t2Stop) |> ...,
  from(bucket: "1hour_bucket") |> range(start: t3Start, stop: t3Stop) |> ...,
])
```

**Important**: The initial plan used zero-width ranges (`stop: v.timeRangeStart`)
for inactive tiers, but InfluxDB rejects these with "cannot query an empty range".

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

### Humidity fields

Analytics tiers store humidity with `hum_` prefix (e.g. `hum_Hilla`, `hum_Niila`).
The Humidity panel (21) uses `strings.trimPrefix` in Flux to strip the prefix
so legend names match the raw `humidities` measurement.

### Fields unique to specific tiers

- `phase1_power`, `phase2_power`, `phase3_power`, `total_power` -- raw Shelly EM3 only
- `grid_voltage_avg`, `grid_current_avg`, `grid_power_factor_avg` -- emeters_5min only
- `consumption_max`, `solar_yield_max`, `grid_power_max` -- analytics_1hour only
- Cost/energy flow fields (`net_cost`, `solar_to_consumption`, etc.) -- analytics only

## Implementation Phases

### Phase 0: Add missing fields to analytics aggregators [DONE]

Added `energy_import_avg`, `energy_export_avg`, and humidity data (with `hum_`
prefix) to `analytics_15min` and `analytics_1hour` aggregators.

Files modified:
- `src/aggregation/analytics_base.py` (added `_fetch_humidities_data`, updated `_add_weather_and_temperature_fields`)
- `src/aggregation/analytics_15min.py`
- `src/aggregation/analytics_1hour.py`
- `src/aggregation/metric_calculators.py` (added `extract_field` helper)

Backfill: use `deployment/backfill_aggregation.py --days 90 --skip-5min`
(bulk mode, ~20 min for 30 days).

### Phase 1a: Shelly EM3 Phase Power (Panel 24) [DONE]

Per-phase power data only exists in raw tier. No fallback to aggregated data.

- Window <= 48h: query `shelly_em3_emeters_raw_staging`
- Window > 48h: return empty (panel shows "No data")
- Panel description: "Raw phase data -- zoom to < 48h for data"

### Phase 1b: CheckWatt (Panel 16) [DONE]

3-tier switching with field renaming between raw and aggregated tiers.

- Window <= 48h: `checkwatt_staging` (fields: SolarYield, BatteryCharge, etc.)
- Window <= 8d: `emeters_5min_staging` (fields renamed via `map()` to match raw names)
- Window > 8d: `analytics_15min_staging` (same rename)

### Phase 2a: Energy Meters (Panel 14) [DONE]

3-tier switching. Panel title updated from "Energy Meters (5min)" to "Energy Meters".

- Window <= 48h: `emeters_5min_staging`
- Window <= 8d: `analytics_15min_staging`
- Window > 8d: `analytics_1hour_staging`

Field names identical across all tiers. Uses shared `fields` function.

### Phase 2b: Solar & Battery Energy Flow (Panel 20) [DONE]

2-tier switching.

- Window <= 8d: `analytics_15min_staging`
- Window > 8d: `analytics_1hour_staging`

### Phase 3: Stat Panels (Panels 4-11) [DONE]

2-tier switching. Branches only filter; sum/map applied once after union.

- Window <= 8d: `analytics_15min_staging`
- Window > 8d: `analytics_1hour_staging`

Panels 2-3 (Consumption Effect, Avg. Actual Price) stay on `analytics_1hour`
because they join with hourly spot prices.

### Phase 4: Temperature & Humidity Panels (Panels 15, 17, 19, 21) [DONE]

- Panels 15, 17, 19: 3-tier (raw temperatures / analytics_15min / analytics_1hour)
- Panel 21 (Humidity): 3-tier with `hum_` prefix stripping via `strings.trimPrefix`
- Panel 15 query B (weather forecast) unchanged

### Heating Schedule (Panel 23) [DONE]

Not part of the original plan, but improved during implementation:
- Heating schedule shown as orange filled bands (stepAfter, 25% opacity)
- Spot price shown as blue line on right axis
- Fixed timestamp bug: `fromtimestamp()` now uses UTC-aware datetime

### Panels Not Changed (Reference Data)

These panels query reference buckets with their own natural cadence:

- Avg. Spot Price (Panel 1) -- `spotprice_staging`
- Consumption Effect (Panel 2) -- `analytics_1hour_staging` + `spotprice_staging`
- Avg. Actual Price (Panel 3) -- `analytics_1hour_staging` + `spotprice_staging`
- Spot Price chart (Panel 12) -- `spotprice_staging`
- Weather Forecast (Panel 13) -- `weather_staging`
- Cost per Hour (Panel 18) -- `analytics_1hour_staging` (naturally hourly)
- Wind Power (Panel 22) -- `windpower_staging`
- Cost per Day (Panel 25) -- `analytics_1hour_staging` (aggregated to daily)

## Bugs Found & Fixed During Implementation

1. **Empty range error**: InfluxDB rejects `range(start: X, stop: X)`. Fixed by
   using epoch fallback ranges (1970-01-01) for inactive tiers.

2. **Heating schedule timestamps**: `datetime.fromtimestamp()` created naive local
   time interpreted as UTC, shifting schedule 3 hours. Fixed in `program_generator.py`.

3. **Humidity not in analytics**: `humidities` measurement was never aggregated.
   Added `_fetch_humidities_data` with `hum_` prefix to analytics pipeline.

4. **NaT crash in schedule builder**: `schedule_builder.py:140` occasionally gets
   pandas NaT values from the optimizer. Pre-existing bug, not yet fixed.

## Deployment

Dashboard JSON: `grafana/dashboards/new_staging_dashboard.json`

Deploy via Grafana API:
```bash
python -c "
import json, os, requests
from dotenv import load_dotenv
load_dotenv()
with open('grafana/dashboards/new_staging_dashboard.json') as f:
    dashboard = json.load(f)
url = os.getenv('GRAFANA_URL') + '/api/dashboards/db'
headers = {'Authorization': 'Bearer ' + os.getenv('GRAFANA_API_KEY'), 'Content-Type': 'application/json'}
r = requests.post(url, headers=headers, json={'dashboard': dashboard, 'overwrite': True})
print(r.status_code, r.json())
"
```
