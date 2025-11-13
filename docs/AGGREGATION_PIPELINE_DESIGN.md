# Aggregation Pipeline Design

## Overview

This document describes the design for multi-level data aggregation pipeline to improve query performance and provide pre-joined analytics data.

**Problem:** Querying raw 1-minute data for long periods (1 month, 1 year) is too slow due to:
- Large number of data points (43,200 points/month at 1-min resolution)
- Expensive joins across multiple buckets at query time
- High load on InfluxDB

**Solution:** Create pre-aggregated buckets with:
- Reduced time resolution (5min, 15min, 1hour)
- Pre-joined data from multiple sources
- Pre-calculated metrics (costs, efficiency ratios)

---

## Bucket Architecture

### Raw Data Buckets (Short Retention: 30 days)

**`checkwatt_full_data`**
- Source: CheckWatt API
- Resolution: 1 minute (delta values)
- Fields: Battery_SoC, BatteryCharge, BatteryDischarge, EnergyImport, EnergyExport, SolarYield
- Purpose: Raw battery and solar data from CheckWatt CM10

**`shelly_em3_emeters_raw`**
- Source: Shelly 3EM energy meter
- Resolution: 1 minute (instantaneous values)
- Fields: 25 fields including per-phase power/voltage/current, energy totals
- Purpose: High-resolution grid consumption and voltage monitoring

**`temperatures`** (existing)
- Source: Temperature sensors
- Resolution: Variable
- Purpose: Indoor and outdoor temperature measurements

**`weather`** (existing)
- Source: FMI weather API
- Resolution: 1 hour forecast
- Purpose: Weather forecasts (temperature, solar radiation, wind)

**`spotprice`** (existing)
- Source: spot-hinta.fi API
- Resolution: 1 hour
- Purpose: Electricity spot prices (buy and sell)

### Aggregated Buckets (Long Retention: 1-5 years)

---

## `emeters_5min` Bucket

**Purpose:** 5-minute aggregated energy data with battery netting
**Retention:** 5 years
**Resolution:** 5 minutes
**Measurement:** `energy`

### Fields

**Energy averages (W):**
- `solar_yield_avg` - Solar production power
- `emeter_avg` - Grid import power (from Shelly EM3)
- `consumption_avg` - Total consumption = emeter + solar + battery_discharge - battery_charge
- `battery_charge_avg` - Battery charging power
- `battery_discharge_avg` - Battery discharging power
- `energy_import_avg` - CheckWatt grid import
- `energy_export_avg` - CheckWatt grid export (netted)
- `cw_emeter_avg` - CheckWatt net grid power = import - export

**Energy deltas (Wh over 5 minutes):**
- `solar_yield_diff` - Energy produced
- `emeter_diff` - Energy imported from grid
- `consumption_diff` - Total energy consumed
- `battery_charge_diff` - Energy stored in battery
- `battery_discharge_diff` - Energy taken from battery

**Battery state:**
- `Battery_SoC` - State of charge (%)

**Grid metrics (from Shelly EM3, averaged over 5 minutes):**
- `grid_voltage_avg` - Average voltage across 3 phases (V)
- `grid_current_avg` - Average current (A)
- `grid_power_factor_avg` - Average power factor

**Metadata:**
- `ts_diff` - Actual time difference between measurements (seconds)

**Total: ~20 fields**

### Data Sources
- CheckWatt: Battery and solar data (1-min deltas → aggregated)
- Shelly EM3: Grid consumption and voltage (1-min snapshots → averaged)

### Aggregation Logic
```
solar_yield_avg = sum(CheckWatt.SolarYield for 5min) / 300s
emeter_avg = (Shelly.net_total_energy[end] - Shelly.net_total_energy[start]) / 300s
consumption_avg = cw_emeter_avg + solar_yield_avg + battery_discharge_avg - battery_charge_avg
```

### Aggregation Schedule
- **Frequency:** Every 5 minutes
- **Timing:** Run at :00, :05, :10, :15, :20, :25, :30, :35, :40, :45, :50, :55
- **Lookback:** Process previous 5-minute window

---

## `analytics_15min` Bucket

**Purpose:** 15-minute joined analytics with energy, prices, weather, and temperatures
**Retention:** 5 years
**Resolution:** 15 minutes
**Measurement:** `analytics`

### Fields

**Energy (from emeters_5min, aggregated to 15min):**
- `solar_yield_avg` (W) - Average solar production
- `consumption_avg` (W) - Average consumption
- `emeter_avg` (W) - Average grid import
- `battery_charge_avg` (W) - Average battery charging
- `battery_discharge_avg` (W) - Average battery discharging
- `Battery_SoC` (%) - Battery state of charge (last value)
- `solar_yield_sum` (Wh) - Total solar energy produced in 15min
- `consumption_sum` (Wh) - Total energy consumed in 15min
- `emeter_sum` (Wh) - Total grid energy imported in 15min

**Spot prices (from spotprice):**
- `price_total` (c/kWh) - Total electricity buy price
- `price_sell` (c/kWh) - Electricity sell price

**Calculated costs (15min window):**
- `electricity_cost` (EUR) - Cost of consumed electricity from grid
- `solar_export_revenue` (EUR) - Revenue from exported solar energy
- `net_cost` (EUR) - Net electricity cost (cost - revenue)
- `self_consumption_ratio` (%) - Portion of solar used directly vs exported

**Weather (from weather):**
- `air_temperature` (C) - Outdoor air temperature
- `cloud_cover` (%) - Cloud coverage
- `solar_radiation` (W/m2) - Solar radiation forecast
- `wind_speed` (m/s) - Wind speed

**Indoor temperatures (from temperatures):**
- `PaaMH` (C) - Main heating circuit supply temperature
- `Ulkolampo` (C) - Outdoor temperature sensor
- `PalMH` (C) - Main heating circuit return temperature

**Heating control (if available):**
- `pump_state` - Heating pump state (ON/OFF/EVU/ALE)
- `heating_priority` - Calculated heating priority value

**Total: ~30 fields**

### Data Sources
1. emeters_5min: Energy data (3x 5-min windows aggregated)
2. spotprice: Electricity prices (15-min slice of 1-hour data)
3. weather: Weather forecast (15-min slice of 1-hour data)
4. temperatures: Indoor/outdoor temperatures (averaged over 15min)
5. load_control: Heating pump state (if exists)

### Aggregation Logic
```
# Energy: Aggregate 3x 5-min windows from emeters_5min
solar_yield_avg = mean(emeters_5min.solar_yield_avg for 15min)
consumption_sum = sum(emeters_5min.consumption_diff)

# Costs: Calculate based on consumption and prices
electricity_cost = (emeter_sum / 1000) * (price_total / 100)  # Convert Wh→kWh, c→EUR
solar_export_revenue = (export_sum / 1000) * (price_sell / 100)
net_cost = electricity_cost - solar_export_revenue

# Self-consumption: How much solar was used directly
self_consumption_ratio = (solar_yield_sum - export_sum) / solar_yield_sum * 100
```

### Aggregation Schedule
- **Frequency:** Every 15 minutes
- **Timing:** Run at :00, :15, :30, :45
- **Lookback:** Process previous 15-minute window
- **Dependencies:** Requires emeters_5min to be populated first

---

## `analytics_1hour` Bucket

**Purpose:** 1-hour joined analytics for long-term trends and monthly/yearly reports
**Retention:** 5 years (or longer for historical analysis)
**Resolution:** 1 hour
**Measurement:** `analytics`

### Fields

**Same structure as analytics_15min, but aggregated to 1-hour windows**

**Energy (from emeters_5min or analytics_15min, aggregated to 1 hour):**
- `solar_yield_avg` (W)
- `consumption_avg` (W)
- `emeter_avg` (W)
- `battery_charge_avg` (W)
- `battery_discharge_avg` (W)
- `Battery_SoC` (%)
- `solar_yield_sum` (Wh) - Total solar in 1 hour
- `consumption_sum` (Wh) - Total consumption in 1 hour
- `emeter_sum` (Wh) - Total grid import in 1 hour

**Additional hourly metrics:**
- `consumption_max` (W) - Peak consumption in hour
- `solar_yield_max` (W) - Peak solar production in hour
- `grid_power_max` (W) - Peak grid power draw

**Spot prices:**
- `price_total` (c/kWh) - Hourly spot price
- `price_sell` (c/kWh) - Hourly sell price
- `price_total_avg` (c/kWh) - Average if price varies within hour

**Calculated costs (1-hour window):**
- `electricity_cost` (EUR)
- `solar_export_revenue` (EUR)
- `net_cost` (EUR)
- `self_consumption_ratio` (%)

**Weather:**
- `air_temperature` (C)
- `cloud_cover` (%)
- `solar_radiation` (W/m2)
- `wind_speed` (m/s)

**Indoor temperatures:**
- `PaaMH` (C)
- `Ulkolampo` (C)
- `PalMH` (C)

**Total: ~30 fields**

### Data Sources
- Option A: Aggregate from analytics_15min (4x 15-min windows)
- Option B: Aggregate directly from emeters_5min (12x 5-min windows)
- Plus: spotprice, weather, temperatures (1-hour resolution matches source)

### Aggregation Schedule
- **Frequency:** Every hour
- **Timing:** Run at :05 past the hour (e.g., 01:05, 02:05, ...)
- **Lookback:** Process previous full hour
- **Dependencies:** Can run after emeters_5min or analytics_15min

---

## Implementation Plan

### Phase 1: Basic Energy Aggregation
1. ✅ Create Shelly EM3 collector (1-min raw data)
2. ✅ Create systemd service for Shelly EM3 collection
3. ⏳ Implement `emeters_5min` aggregator
4. ⏳ Create systemd service for 5-min aggregation
5. ⏳ Test and validate energy calculations

### Phase 2: Analytics Aggregation
1. ⏳ Implement `analytics_15min` aggregator
2. ⏳ Create systemd service for 15-min aggregation
3. ⏳ Implement `analytics_1hour` aggregator
4. ⏳ Create systemd service for hourly aggregation
5. ⏳ Test query performance improvements

### Phase 3: Enhancements
1. ⏳ Add per-phase data from Shelly EM3 (optional)
2. ⏳ Add FCR-D/N frequency data when available
3. ⏳ Add calculated efficiency metrics
4. ⏳ Create backfill scripts for historical data
5. ⏳ Update Grafana dashboards to use aggregated buckets

---

## Open Questions

1. **Shelly EM3 per-phase data:** Should we include all 21 per-phase fields in emeters_5min?
   - Pros: Complete power quality monitoring, detect phase imbalances
   - Cons: More storage, more fields to aggregate
   - **Decision:** TBD

2. **FCR-D/N frequency data:** Include grid frequency when available?
   - Needed for frequency containment reserve logging
   - Requires separate frequency measurement device (Waveshare gateway or separate monitor)
   - **Decision:** Add as separate field when frequency monitoring is implemented

3. **Calculated metrics:** Additional derived fields?
   - Grid independence ratio (%)
   - Battery cycle efficiency (%)
   - Cost per kWh self-produced vs grid
   - **Decision:** TBD based on dashboard requirements

4. **Aggregation order:** Should analytics_1hour aggregate from analytics_15min or emeters_5min?
   - From 15min: Simpler, less data to process
   - From 5min: More accurate, can calculate min/max/peak values
   - **Decision:** Start with 5min for accuracy, optimize later if needed

5. **Retention policies:**
   - Raw (1-min): 30 days - Confirmed
   - emeters_5min: 5 years - TBD
   - analytics_15min: 5 years - TBD
   - analytics_1hour: Unlimited/10 years? - TBD

---

## Performance Benefits

### Query Performance Comparison

**Scenario: Query 1 month of data**

| Resolution | Data Points | Query Time (est.) | Reduction |
|-----------|-------------|-------------------|-----------|
| 1 minute (raw) | 43,200 | ~30-60s | Baseline |
| 5 minutes | 8,640 | ~6-12s | 5x faster |
| 15 minutes | 2,880 | ~2-4s | 15x faster |
| 1 hour | 720 | ~0.5-1s | 60x faster |

**Scenario: Query 1 year of data**

| Resolution | Data Points | Query Time (est.) | Reduction |
|-----------|-------------|-------------------|-----------|
| 1 minute (raw) | 525,600 | Timeout/slow | Baseline |
| 5 minutes | 105,120 | ~60-120s | 5x faster |
| 15 minutes | 35,040 | ~20-40s | 15x faster |
| 1 hour | 8,760 | ~5-10s | 60x faster |

### Storage Comparison

**Storage per year (approximate):**
- Raw 1-min: ~50 MB/year (25 fields × 525k points)
- emeters_5min: ~10 MB/year (20 fields × 105k points)
- analytics_15min: ~15 MB/year (30 fields × 35k points)
- analytics_1hour: ~4 MB/year (30 fields × 8.8k points)

**Total with retention:**
- Raw (30 days only): ~4 MB
- Aggregated (5 years): ~145 MB (29 MB/year × 5 years)
- **Grand total: ~150 MB for 5 years of data** (vs ~250 MB with raw data)

---

## Notes

- This design follows the InfluxDB best practice of "write raw, aggregate to faster buckets"
- Pre-joined data eliminates expensive joins at query time
- Multiple resolution levels allow choosing appropriate granularity for each use case
- Dashboard queries should primarily use analytics_15min or analytics_1hour for performance

---

## Related Documents

- [Emeters Data Collection](./EMETERS_COLLECTION.md) (to be created)
- [Frequency Monitoring for FCR-D/N](./FREQUENCY_MONITORING.md) (to be created)

---

*Last updated: 2025-11-13*
*Status: Design phase - awaiting implementation*
