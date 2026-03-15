# RedHouse InfluxDB Database Schema

This document describes all InfluxDB buckets and measurement schemas used by the
RedHouse home automation system, covering both the old system (standalone scripts)
and the new refactored system (Python package under src/).

InfluxDB uses a time-series data model. Each **bucket** holds one or more
**measurements**. Each data point has a timestamp, optional string **tags**
(indexed) and numeric or string **fields** (not indexed).

Organization: `area51`
InfluxDB URL: configured via `INFLUXDB_URL` environment variable

---

## Architecture Overview

### Old System

The old system runs a set of standalone Python scripts on the Raspberry Pi out
of `/home/pi/wibatemp/` and `/home/pi/.fissio/`:

| Script | Role |
|---|---|
| `.fissio/energy_to_fissio.py` | Polls SMA solar inverter (pysma) + Shelly EM3, writes 5-min energy deltas to `emeters` bucket and Fissio flat-file (cron runs every 5 min) |
| `wibatemp/checkwatt_dataloader.py` | Fetches last hour of CheckWatt API data on every run, writes raw 1-min power data to `checkwatt_full_data`, then merges with `emeters` bucket via pandas (cron runs every 5 min) |
| `wibatemp/wibatemp.py` | Polls 1-wire DS18B20 sensors + Shelly HT REST callbacks, writes to `temperatures` bucket |
| `wibatemp/shelly_ht_to_fissio_rest_api.py` | HTTP server (port 8008) that receives Shelly HT push callbacks and stores to `temperature_status.json` for `wibatemp.py` to read |
| `wibatemp/get_weather.py` | Downloads FMI Harmonie forecast, writes to `weather` bucket |
| `wibatemp/generate_heating_program.py` | Reads `emeters`, `spotprice`, `weather`; generates daily heating schedule JSON (no InfluxDB write) |
| `wibatemp/water_temp_controller.py` | Reads `emeters`, `spotprice`; controls hot water relay via Shelly |
| `wibatemp/predict_solar_yield.py` | Generates solar yield prediction JSON files under `/home/pi/solar_prediction/` |

Cron scheduling (from `wibatemp/crontab.list`):

| Schedule | Script | Description |
|---|---|---|
| `@reboot` | `mlp_control.sh restore` | Restores multi-load controller state on boot |
| `@reboot` | `set_i2c_wire_configuration.sh` | Hardware setup (1-wire bus) |
| `@reboot` | `start_shelly_ht_to_fissio_rest_api.sh` | Starts the Shelly HT HTTP server |
| `* * * * *` | `run_wibatemp.sh` | Temperature/humidity collection (every minute) |
| `4,9,14,...,59 * * * *` | `run_energy_to_fissio.sh` | Energy meter collection (every 5 min) |
| `1,6,11,...,56 * * * *` | `run_checkwatt_dataloader.sh` | Fetches last 1h from CheckWatt API + merges into emeters (every 5 min) |
| `59 13,14,15 * * *` + `29 14,15 * * *` | `run_spot_price_getter.sh` | Spot price collection (multiple attempts ~14-16h) |
| `5 16 * * *` | `run_generate_heating_program.sh` | Daily heating program generation at 16:05 |
| `*/15 * * * *` | `run_execute_heating_program.sh` | Heating program execution (every 15 min) |
| `23 */2 * * *` | `mlp_cycle_evu_off_hourly.sh` | EVU-off cycling every 2 hours |
| `2 * * * *` | `get_weather.py` | Weather forecast download (hourly) |
| `3 * * * *` | `predict_solar_yield.py` | Solar yield prediction (hourly) |
| `4 * * * *` | `run_windpowergetter.sh` | Wind power data (hourly) |
| `01 06 * * *` | `start_shelly_ht_to_fissio_rest_api.sh` | Daily restart of Shelly HT server |

Note: `run_water_temp_controller.sh` is currently disabled (commented out).

The schema is single-bucket for energy: all energy metrics co-located in
`emeters`, measurement `energy`. Temperatures and weather use the same bucket
names as the new system.

### New System

The new refactored system uses a layered three-tier architecture:

```
Tier 1 - Raw Data (1-minute cadence)
    checkwatt_full_data      <- CheckWatt API (solar, battery, grid power)
    shelly_em3_emeters_raw   <- Shelly EM3 device (per-phase grid measurements)

Tier 2 - 5-Minute Aggregation
    emeters_5min             <- Aggregated from checkwatt_full_data + shelly_em3_emeters_raw

Tier 3 - Analytics (aggregated from emeters_5min + reference data)
    analytics_15min          <- 15-min windows: energy + costs + weather + temperatures
    analytics_1hour          <- 1-hour windows: energy + costs + peak power + weather + temps

Reference Data (independent write cadence)
    temperatures             <- DS18B20 1-wire and Shelly HT sensors (~1 min)
    weather                  <- FMI forecast (15-min intervals, updated hourly)
    spotprice                <- Hourly electricity spot prices (updated ~14:00 EET daily)
    windpower                <- Finnish grid wind power (hourly, Fingrid + FMI)

Control
    load_control             <- Heating program schedules (plan and actual commands)
```

---

## Old System Buckets

### Bucket: `emeters` (Old system)

**Status:** Currently active. Written by the old system scripts and read by
the old heating optimizer, water temperature controller, and Grafana dashboards.

**Written by:** `.fissio/energy_to_fissio.py` (5-min intervals, cron) and
supplemented by `wibatemp/checkwatt_dataloader.py` (which re-writes merged
5-min averages back into the same bucket).

**Measurement: `energy`**

Fields written by `energy_to_fissio.py` (primary 5-min data):

| Field | Type | Unit | Description |
|---|---|---|---|
| `emeter_avg` | float | Wh/s | Net grid power (emeter_diff / ts_diff). Positive = import |
| `consumption_avg` | float | Wh/s | Total consumption ((emeter_diff + solar_yield_diff) / ts_diff) |
| `solar_yield_avg` | float | Wh/s | Solar generation (solar_yield_diff / ts_diff) |
| `ts_diff` | int | s | Seconds between current and previous reading |
| `emeter_diff` | float | Wh | Net grid energy delta (Shelly EM3 net counter difference) |
| `consumption_diff` | float | Wh | Consumption energy delta (emeter_diff + solar_yield_diff) |
| `solar_yield_diff` | float | Wh | Solar energy delta (SMA total_yield counter difference) |
| `solar_yield_latest_total` | float | Wh | Cumulative SMA total_yield (ever-increasing) |
| `solar_yield_previous_total` | float | Wh | Previous cumulative SMA total_yield |
| `emeter_latest_total` | float | Wh | Cumulative Shelly EM3 net total (total - total_returned) |
| `emeter_previous_total` | float | Wh | Previous cumulative Shelly EM3 net total |
| `latest_power_source` | string | - | `"external"` or `"backup"` (UPS/battery status) |
| `previous_power_source` | string | - | Previous power source value |
| `solar_yield_avg_prediction` | float | Wh/s | Solar yield prediction (written separately for future timestamps) |

Additional fields written back by `checkwatt_dataloader.py` (5-min merged data):

| Field | Type | Unit | Description |
|---|---|---|---|
| `battery_charge_avg` | float | W | Battery charge power (from CheckWatt, unit-converted) |
| `battery_discharge_avg` | float | W | Battery discharge power (from CheckWatt) |
| `energy_import_avg` | float | W | Grid import power (CheckWatt, netted) |
| `energy_export_avg` | float | W | Grid export power (CheckWatt, netted) |
| `cw_emeter_avg` | float | W | Net grid power from CheckWatt (import - export) |
| `battery_charge_diff` | float | Wh | Battery charge energy delta |
| `battery_discharge_diff` | float | Wh | Battery discharge energy delta |
| `energy_import_diff` | float | Wh | Grid import energy delta |
| `energy_export_diff` | float | Wh | Grid export energy delta |

**Notes:**
- Solar data came from SMA inverter via `pysma` (cumulative kWh counter read as
  `total_yield`, multiplied by 1000 to get Wh).
- Grid data came from Shelly EM3 net total (`sum of (emeter['total'] - emeter['total_returned'])`
  across all three phases), also cumulative Wh.
- Counter resets were detected when `abs(emeter_diff / ts_diff) > 20.0 Wh/s`
  (equivalent to 72 kW sustained) and handled by zeroing the previous value.
- Solar yield was not read at night (00:00-03:00 local time) to avoid SMA
  inverter connection errors.
- The old `generate_heating_program.py` reads `solar_yield_avg_prediction` from
  this bucket to plan heating schedules.
- The old `get_weather.py` uses the field name `"Air temperature"` (with capital
  and space) when querying weather; see weather bucket notes below.

---

### Bucket: `checkwatt_full_data` (same name in both systems)

Written by the old `wibatemp/checkwatt_dataloader.py` and the new
`src/data_collection/checkwatt.py`. Schema is identical. See the New System
section below.

---

## New System Buckets

### Bucket: `temperatures`

**Default name:** `temperatures`
**Env var:** `INFLUXDB_BUCKET_TEMPERATURES`

Stores temperature readings from DS18B20 1-wire sensors and Shelly HT
humidity/temperature sensors. Used by both old and new systems with the same
bucket name and measurement names, but **field names changed** between systems
(see notes).

**Measurement: `temperatures`**

One field per sensor. Field names are derived from sensor hardware ID via
the `SENSOR_NAMES` mapping in [src/data_collection/temperature.py](../src/data_collection/temperature.py).

| Field (new system) | Field (old system) | Type | Unit | Location |
|---|---|---|---|---|
| `Hilla` | `Hilla` | float | degC | Room Hilla |
| `Niila` | `Niila` | float | degC | Room Niila |
| `Savupiippu` | `Savupiippu` | float | degC | Chimney base |
| `Valto` | `Valto` | float | degC | Room Valto |
| `PaaMH` | `PaaMH` (was `PaaaMH`) | float | degC | Master bedroom |
| `Pukuhuone` | `Pukuhuone` | float | degC | Changing room |
| `Kirjasto` | `Kirjasto` | float | degC | Library |
| `Eteinen` | `Eteinen` | float | degC | Entrance hall |
| `Keittio` | `Keittio` (was `Keittio`) | float | degC | Kitchen |
| `Tyohuone` | `Tyohuone` (was `Tyohuone`) | float | degC | Office |
| `Leffahuone` | `Leffahuone` | float | degC | Living room |
| `Kayttovesi ylh` | `Kayttovesi ylh` | float | degC | Domestic hot water upper |
| `Kayttovesi alh` | `Kayttovesi alh` | float | degC | Domestic hot water lower |
| `Ulkolampo` | `Ulkolampo` (was `Ulkolampo`) | float | degC | Outdoor temperature |
| `Autotalli` | `Autotalli` | float | degC | Garage |
| `PaaMH2` | `PaaMH2` | float | degC | Master bedroom sensor 2 |
| `PaaMH3` | `PaaMH3` | float | degC | Master bedroom sensor 3 |
| `YlakertaKH` | `YlakertaKH` | float | degC | Upper floor bathroom |
| `KeskikerrosKH` | `KeskikerrosKH` | float | degC | Middle floor bathroom |
| `AlakertaKH` | `AlakertaKH` | float | degC | Lower floor bathroom |

Only sensors that successfully read are included in each point. Failed sensors
are silently omitted.

**Measurement: `humidities`**

Written to the same `temperatures` bucket. Same sensor naming convention as
`temperatures`, but storing relative humidity (%) from Shelly HT sensors.
The Shelly HT devices push data to `shelly_ht_to_fissio_rest_api.py` (port
8008) in the old system; in the new system they are read the same way via
the `InfluxClient.write_humidities()` method.

**Notes (old vs new field names):**
- The old `wibatemp.py` used Unicode Finnish characters for some field names
  (e.g. `"PaaMH"` was stored as `"PaaaMH"`, `"Kayttovesi ylh"` as
  `"Kayttovesi ylh"`, `"Ulkolampo"` as `"Ulkolampo"`).
- The new system standardized all field names to ASCII. Historical data written
  by the old system may have Unicode field names for some sensors.

---

### Bucket: `weather`

**Default name:** `weather`
**Env var:** `INFLUXDB_BUCKET_WEATHER`

Stores FMI (Finnish Meteorological Institute) weather forecasts from the
Harmonie surface model. Used by both old and new systems with the same bucket
name and measurement name.

**Measurement: `weather`**

Fields are raw FMI parameter names from the `fmiopendata` library.
The field set depends on what FMI returns for query
`fmi::forecast::harmonie::surface::point::multipointcoverage`.

| Field | Type | Unit | Description |
|---|---|---|---|
| `Air temperature` | float | degC | Air temperature (old system field name) |
| `air_temperature` | float | degC | Air temperature (new system - same data, different name from fmiopendata) |
| `wind_speed` | float | m/s | Wind speed |
| `wind_direction` | float | deg | Wind direction |
| `cloud_cover` | float | % | Cloud cover (0-100) |
| `solar_radiation` | float | W/m2 | Global solar radiation |
| `humidity` | float | % | Relative humidity |
| `precipitation` | float | mm/h | Precipitation intensity |
| `pressure` | float | hPa | Air pressure |
| (other FMI fields) | float | varies | All fields returned by FMI except `Geopotential height` |

**Notes:**
- The old `wibatemp/get_weather.py` queries the field as `"Air temperature"`
  (capitalized, with space). The new system receives `"air_temperature"` (snake
  case) from the same FMI query - this is a change in the `fmiopendata` library
  output format between versions.
- Both old `generate_heating_program.py` and new `src/control/heating_data_fetcher.py`
  filter on the air temperature field when building heating schedules.
- Cadence: FMI returns 15-min interval forecasts. Updated hourly by cron.
- Location configured via `WEATHER_LATLON` env var.
  Old system hardcodes the location coordinates in `get_weather.py`.

---

### Bucket: `spotprice`

**Default name:** `spotprice`
**Env var:** `INFLUXDB_BUCKET_SPOTPRICE`

Stores hourly electricity spot prices from the spot-hinta.fi API.
The schema is the same in both old and new systems.

**Measurement: `spot`**

| Field | Type | Unit | Description |
|---|---|---|---|
| `price` | float | EUR/kWh | Raw spot price without tax (`PriceNoTax` from API) |
| `price_sell` | float | EUR/kWh | Production buyback price (`price - 0.01 * SPOT_PRODUCTION_BUYBACK_MARGIN`) |
| `price_withtax` | float | EUR/kWh | Spot price including VAT (`SPOT_VALUE_ADDED_TAX * price`) |
| `price_total` | float | EUR/kWh | Total consumer price including all fees and taxes |

**Price formula for `price_total`:**
```
transfer_price = SPOT_TRANSFER_NIGHT_PRICE  (22:00-07:00)
               = SPOT_TRANSFER_DAY_PRICE    (07:00-22:00)
price_total = price_withtax + 0.01 * (SPOT_SELLERS_MARGIN + transfer_price + SPOT_TRANSFER_TAX_PRICE)
```

**Cadence:** One point per hour. Tomorrow's prices are typically available
from ~14:00 EET. The collector skips fetching if tomorrow's prices are already
stored.

---

### Bucket: `checkwatt_full_data`

**Default name:** `checkwatt_full_data`
**Env var:** `INFLUXDB_BUCKET_CHECKWATT`

Stores 1-minute resolution solar, battery, and grid power data from the
CheckWatt API (EnergyInBalance platform). Schema is identical between old
(`wibatemp/checkwatt_dataloader.py`) and new (`src/data_collection/checkwatt.py`)
systems.

**Measurement: `checkwatt`**

The CheckWatt API `delta` grouping mode returns **average power in Watts** for
each 1-minute interval (not cumulative energy in Wh).

| Field | Type | Unit | Description |
|---|---|---|---|
| `Battery_SoC` | float | % | Battery state of charge (0-100) |
| `BatteryCharge` | float | W | Battery charging power |
| `BatteryDischarge` | float | W | Battery discharging power |
| `EnergyImport` | float | W | Grid import power |
| `EnergyExport` | float | W | Grid export power |
| `SolarYield` | float | W | Solar panel generation power |

**Notes:**
- The last record of each API fetch has only `Battery_SoC`; other fields are
  excluded because the final delta interval is incomplete.
- In test environments the measurement name is `checkwatt_v2` to avoid field
  type conflicts with old test data.
- Meter IDs are configured via `CHECKWATT_METER_IDS` (comma-separated). The
  order must match: Battery_SoC, BatteryCharge, BatteryDischarge, EnergyImport,
  EnergyExport, SolarYield.

---

### Bucket: `shelly_em3_emeters_raw`

**Default name:** `shelly_em3_emeters_raw`
**Env var:** `INFLUXDB_BUCKET_SHELLY_EM3_RAW`

Stores raw measurements from the Shelly EM3 three-phase energy meter polled
directly via its HTTP `/status` endpoint. This bucket does not exist in the
old system - the old system reads only the net total from Shelly EM3 and does
not store raw phase data.

**Measurement: `shelly_em3`**

Per-phase instant measurements (phases 1, 2, 3):

| Field | Type | Unit | Description |
|---|---|---|---|
| `phase1_power` | float | W | Phase 1 instant power |
| `phase2_power` | float | W | Phase 2 instant power |
| `phase3_power` | float | W | Phase 3 instant power |
| `phase1_current` | float | A | Phase 1 current |
| `phase2_current` | float | A | Phase 2 current |
| `phase3_current` | float | A | Phase 3 current |
| `phase1_voltage` | float | V | Phase 1 voltage |
| `phase2_voltage` | float | V | Phase 2 voltage |
| `phase3_voltage` | float | V | Phase 3 voltage |
| `phase1_pf` | float | - | Phase 1 power factor |
| `phase2_pf` | float | - | Phase 2 power factor |
| `phase3_pf` | float | - | Phase 3 power factor |
| `phase1_total` | float | Wh | Phase 1 cumulative energy consumed (ever-increasing) |
| `phase2_total` | float | Wh | Phase 2 cumulative energy consumed (ever-increasing) |
| `phase3_total` | float | Wh | Phase 3 cumulative energy consumed (ever-increasing) |
| `phase1_total_returned` | float | Wh | Phase 1 cumulative energy returned/exported |
| `phase2_total_returned` | float | Wh | Phase 2 cumulative energy returned/exported |
| `phase3_total_returned` | float | Wh | Phase 3 cumulative energy returned/exported |
| `phase1_net_total` | float | Wh | Phase 1 net (total - total_returned) |
| `phase2_net_total` | float | Wh | Phase 2 net (total - total_returned) |
| `phase3_net_total` | float | Wh | Phase 3 net (total - total_returned) |

Aggregate fields (sum across all three phases):

| Field | Type | Unit | Description |
|---|---|---|---|
| `total_power` | float | W | Total instant power all phases |
| `total_energy` | float | Wh | Cumulative total energy all phases (monotonically increasing) |
| `total_energy_returned` | float | Wh | Cumulative exported energy all phases (monotonically increasing) |
| `net_total_energy` | float | Wh | Net cumulative energy (total_energy - total_energy_returned) |

**Notes:**
- `total_energy` and `total_energy_returned` reset to zero on Shelly device
  reboot. Counter resets are detected in the 5-min aggregation step by checking
  for decreases > 10 kWh between consecutive readings.
- Device URL configured via `SHELLY_EM3_URL` environment variable.

---

### Bucket: `emeters_5min`

**Default name:** `emeters_5min`
**Env var:** `INFLUXDB_BUCKET_EMETERS_5MIN`

5-minute aggregated energy data derived from `checkwatt_full_data` and
`shelly_em3_emeters_raw`. Written by `Emeters5MinAggregator` in
[src/aggregation/emeters_5min.py](../src/aggregation/emeters_5min.py).
This bucket is new in the refactored system.

**Measurement: `energy`**

CheckWatt-derived fields (average power over the 5-min window):

| Field | Type | Unit | Description |
|---|---|---|---|
| `solar_yield_avg` | float | W | Average solar generation power |
| `battery_charge_avg` | float | W | Average battery charging power |
| `battery_discharge_avg` | float | W | Average battery discharging power |
| `energy_import_avg` | float | W | Average grid import power |
| `energy_export_avg` | float | W | Average grid export power |
| `cw_emeter_avg` | float | W | Net grid power from CheckWatt (import_avg - export_avg) |
| `Battery_SoC` | float | % | Battery SoC, last reading in window |

CheckWatt-derived energy deltas (Wh over the 5-min window):

| Field | Type | Unit | Description |
|---|---|---|---|
| `solar_yield_diff` | float | Wh | Solar energy generated |
| `battery_charge_diff` | float | Wh | Battery energy charged |
| `battery_discharge_diff` | float | Wh | Battery energy discharged |

Shelly EM3-derived fields:

| Field | Type | Unit | Description |
|---|---|---|---|
| `emeter_avg` | float | W | Net grid power from Shelly EM3 (positive = import) |
| `emeter_diff` | float | Wh | Net grid energy over the window |
| `ts_diff` | float | s | Actual time span covered by Shelly data in the window |
| `grid_voltage_avg` | float | V | Average grid voltage (mean of all 3 phases) |
| `grid_current_avg` | float | A | Average grid current (mean of all 3 phases) |
| `grid_power_factor_avg` | float | - | Average power factor (mean of all 3 phases) |
| `energy_returned_avg` | float | W | Average exported power (from Shelly returned counter) |
| `energy_returned_diff` | float | Wh | Exported energy over the window |

Derived computed fields:

| Field | Type | Unit | Description |
|---|---|---|---|
| `consumption_avg` | float | W | Total consumption: `emeter_avg + solar_yield_avg + battery_discharge_avg - battery_charge_avg` |
| `consumption_diff` | float | Wh | Total consumption energy over the window |

**Notes:**
- CheckWatt power values over 25 kW are treated as erroneous and zeroed with
  a warning log.
- Shelly counter resets (decreases > 10 kWh) are handled by estimating energy
  from averaged instantaneous power around the reset point.
- The window timestamp is the end of the 5-minute aggregation window.

---

### Bucket: `analytics_15min`

**Default name:** `analytics_15min`
**Env var:** `INFLUXDB_BUCKET_ANALYTICS_15MIN`

15-minute analytics combining energy, cost, weather, and temperature data.
Written by `Analytics15MinAggregator` in
[src/aggregation/analytics_15min.py](../src/aggregation/analytics_15min.py).
Reads from: `emeters_5min` (3 x 5-min windows), `spotprice`, `weather`,
`temperatures`. New in the refactored system.

**Measurement: `analytics`**

Energy averages (mean of the 3 x 5-min window averages):

| Field | Type | Unit | Description |
|---|---|---|---|
| `solar_yield_avg` | float | W | Average solar power |
| `consumption_avg` | float | W | Average total consumption |
| `emeter_avg` | float | W | Average net grid power |
| `battery_charge_avg` | float | W | Average battery charge power |
| `battery_discharge_avg` | float | W | Average battery discharge power |

Energy sums (total Wh over 15 min):

| Field | Type | Unit | Description |
|---|---|---|---|
| `solar_yield_sum` | float | Wh | Solar energy generated |
| `consumption_sum` | float | Wh | Total energy consumed |
| `emeter_sum` | float | Wh | Net grid energy (positive = import) |
| `battery_charge_sum` | float | Wh | Battery energy charged |
| `battery_discharge_sum` | float | Wh | Battery energy discharged |
| `export_sum` | float | Wh | Energy exported to grid (from CheckWatt export field) |
| `Battery_SoC` | float | % | Battery state of charge (last value) |

Spot price fields:

| Field | Type | Unit | Description |
|---|---|---|---|
| `price_total` | float | EUR/kWh | Total consumer electricity price for this hour |
| `price_withtax` | float | EUR/kWh | Spot price with VAT (no transfer fees) for this hour |
| `price_sell` | float | EUR/kWh | Production buyback price for this hour |

Cost allocation fields (priority-based energy flow model, EUR):

| Field | Type | Unit | Description |
|---|---|---|---|
| `solar_to_consumption` | float | Wh | Solar energy used directly by loads (priority 1) |
| `solar_to_battery` | float | Wh | Solar energy stored in battery (priority 2) |
| `solar_to_export` | float | Wh | Solar energy exported to grid (remainder) |
| `solar_direct_value` | float | EUR | Avoided import cost from solar direct use |
| `solar_export_revenue` | float | EUR | Revenue from solar export |
| `battery_charge_from_solar_cost` | float | EUR | Opportunity cost of solar-to-battery (foregone export) |
| `grid_to_battery` | float | Wh | Grid energy used to charge battery |
| `battery_charge_from_grid_cost` | float | EUR | Cost of grid-to-battery charging |
| `battery_charge_total_cost` | float | EUR | Total battery charging cost |
| `battery_to_consumption` | float | Wh | Battery discharge to loads |
| `battery_discharge_value` | float | EUR | Value of battery discharge to consumption |
| `battery_to_export` | float | Wh | Battery discharge to grid export |
| `battery_export_revenue` | float | EUR | Revenue from battery export |
| `battery_arbitrage` | float | EUR | Net battery arbitrage benefit (discharge value - charge cost) |
| `grid_import_cost` | float | EUR | Cost of grid energy for remaining consumption |
| `total_electricity_cost` | float | EUR | Total grid import cost |
| `total_solar_savings` | float | EUR | Total savings from solar (direct + export) |
| `net_cost` | float | EUR | Net cost after solar and battery savings |
| `electricity_cost` | float | EUR | Same as `grid_import_cost` (backwards compatibility alias) |

Self-consumption metrics:

| Field | Type | Unit | Description |
|---|---|---|---|
| `solar_direct_sum` | float | Wh | Solar used directly (not via battery, not exported) |
| `self_consumption_ratio` | float | % | `solar_direct_sum / solar_yield_sum * 100` |

Weather fields (mean over the 15-min window from `weather` bucket):

| Field | Type | Unit | Description |
|---|---|---|---|
| `air_temperature` | float | degC | Outside air temperature |
| `cloud_cover` | float | % | Cloud cover |
| `solar_radiation` | float | W/m2 | Global solar radiation |
| `wind_speed` | float | m/s | Wind speed |

Temperature fields: all active sensor readings from the `temperatures` bucket
(mean over the window), using the room name fields listed in the `temperatures`
section.

---

### Bucket: `analytics_1hour`

**Default name:** `analytics_1hour`
**Env var:** `INFLUXDB_BUCKET_ANALYTICS_1HOUR`

Hourly analytics combining energy, cost, weather, and temperature data.
Written by `Analytics1HourAggregator` in
[src/aggregation/analytics_1hour.py](../src/aggregation/analytics_1hour.py).
Reads from: `emeters_5min` (12 x 5-min windows), `spotprice`, `weather`,
`temperatures`. New in the refactored system.

**Measurement: `analytics`**

Contains all the same fields as `analytics_15min` (energy averages, sums,
price, cost allocation, self-consumption, weather, temperatures), plus the
following peak power fields:

| Field | Type | Unit | Description |
|---|---|---|---|
| `consumption_max` | float | W | Peak consumption power in the hour |
| `solar_yield_max` | float | W | Peak solar generation in the hour |
| `grid_power_max` | float | W | Peak net grid import power in the hour |

---

### Bucket: `windpower`

**Default name:** `windpower`
**Env var:** `INFLUXDB_BUCKET_WINDPOWER`

Stores Finnish national wind power production data and forecasts from
Fingrid and FMI. New in the refactored system.

**Measurement: `windpower`**

| Field | Type | Unit | Description |
|---|---|---|---|
| `Production` | int | MW | Actual wind power production (Fingrid variable 75, hourly) |
| `Max capacity` | int | MW | Maximum installed wind power capacity (Fingrid variable 268) |
| `Hourly forecast` | float | MW | Fingrid hourly wind power forecast (variable 245) |
| `Daily forecast` | float | MW | Fingrid daily wind power forecast (variable 246) |
| `FMI forecast` | float | MW | FMI wind power forecast for Finland (converted from kW) |

**Notes:**
- Requires `FINGRID_API_KEY` environment variable.
- Default fetch range: 2 days back to 3 days forward.
- FMI data sourced from `cdn.fmi.fi/products/renewable-energy-forecasts/wind/windpower_fi_latest.json`.

---

### Bucket: `load_control`

**Default name:** `load_control` (staging: `load_control_staging`)
**Env var:** `INFLUXDB_BUCKET_LOAD_CONTROL`

Stores heating program schedules and summary data for Grafana visualization
and execution tracking. New in the refactored system.

**Measurement: `load_control`**

One point per scheduled command per load.

Tags:

| Tag | Values | Description |
|---|---|---|
| `program_date` | YYYY-MM-DD | Date the program was generated for |
| `load_id` | `geothermal_pump`, `garage_heater`, `ev_charger` | Which controllable load |
| `data_type` | `plan`, `actual`, `adjusted` | Planned, executed, or adjusted data |

Fields:

| Field | Type | Unit | Description |
|---|---|---|---|
| `command` | string | - | `ON`, `OFF`, or `EVU` (EVU-OFF = block direct water heating) |
| `power_kw` | float | kW | Load power when ON |
| `is_on` | bool | - | True if command == "ON" |
| `is_evu_off` | bool | - | True if command == "EVU" |
| `priority_score` | float | - | Scheduling priority score for this interval |
| `spot_price_c_kwh` | float | c/kWh | Spot price for this hour |
| `solar_prediction_kwh` | float | kWh | Predicted solar generation for this interval |
| `estimated_cost_eur` | float | EUR | Estimated cost if heating is ON this interval |
| `duration_minutes` | int | min | Duration of this scheduled interval |
| `reason` | string | - | Human-readable reason for this scheduling decision |

**Measurement: `load_control_summary`**

One summary point per program generation run.

Tags:

| Tag | Values | Description |
|---|---|---|
| `program_date` | YYYY-MM-DD | Date the program was generated for |
| `data_type` | `plan`, `actual`, `adjusted` | Data type |

Fields:

| Field | Type | Unit | Description |
|---|---|---|---|
| `avg_temperature_c` | float | degC | Average outdoor temperature for the day |
| `total_heating_hours` | float | h | Total heating hours needed (from heating curve) |
| `total_cost_eur` | float | EUR | Estimated total heating cost for the day |
| `total_heating_intervals` | int | - | Number of ON intervals planned |
| `total_evu_off_intervals` | int | - | Number of EVU-OFF intervals planned |
| `cheapest_price` | float | c/kWh | Cheapest interval price used |
| `most_expensive_price` | float | c/kWh | Most expensive interval price used |
| `average_price` | float | c/kWh | Average price over heating intervals |

---

## Data Flow Diagram

```
OLD SYSTEM
----------
SMA inverter (pysma)         --+
Shelly EM3 (net total only)  --+-[5 min]-> emeters bucket (measurement: energy)
                                             also writes to /home/pi/.fissio/mittaustiedot.txt
                                             and /home/pi/total_energy/*.csv

CheckWatt API                -[5 min, fetches last 1h each run]-> checkwatt_full_data (measurement: checkwatt)
                                                                   then merges with emeters via pandas -> emeters bucket

DS18B20 + Shelly HT (REST)  -[1 min]-> temperatures bucket (measurements: temperatures, humidities)
FMI API                     -[hourly]-> weather bucket (measurement: weather)
spot-hinta.fi API           -[daily]-> spotprice bucket (measurement: spot)


NEW (REFACTORED) SYSTEM
-----------------------
DS18B20 sensors             -[1 min]-> temperatures bucket (measurement: temperatures)
Shelly HT sensors           -[1 min]-> temperatures bucket (measurement: humidities)
FMI API                     -[hourly]-> weather bucket (measurement: weather)
spot-hinta.fi API           -[daily]-> spotprice bucket (measurement: spot)
Fingrid/FMI APIs            -[hourly]-> windpower bucket (measurement: windpower)

CheckWatt API               -[1 min]-> checkwatt_full_data (measurement: checkwatt)
Shelly EM3 HTTP API         -[1 min]-> shelly_em3_emeters_raw (measurement: shelly_em3)

checkwatt_full_data
  + shelly_em3_emeters_raw  -[5 min aggregation]-> emeters_5min (measurement: energy)

emeters_5min + spotprice
  + weather + temperatures  -[15 min aggregation]-> analytics_15min (measurement: analytics)
                            -[1 hour aggregation]-> analytics_1hour (measurement: analytics)

HeatingProgramGenerator     -> load_control (measurements: load_control, load_control_summary)
```

---

## Bucket Name Reference

| Env Variable | Default Name | System | Description |
|---|---|---|---|
| `INFLUXDB_BUCKET_TEMPERATURES` | `temperatures` | Both | Room temperatures and humidities |
| `INFLUXDB_BUCKET_WEATHER` | `weather` | Both | FMI weather forecasts |
| `INFLUXDB_BUCKET_SPOTPRICE` | `spotprice` | Both | Electricity spot prices |
| `INFLUXDB_BUCKET_EMETERS` | `emeters` | Old only | Old system energy meter data |
| `INFLUXDB_BUCKET_CHECKWATT` | `checkwatt_full_data` | Both | 1-min CheckWatt power data |
| `INFLUXDB_BUCKET_SHELLY_EM3_RAW` | `shelly_em3_emeters_raw` | New only | 1-min Shelly EM3 raw data |
| `INFLUXDB_BUCKET_EMETERS_5MIN` | `emeters_5min` | New only | 5-min aggregated energy |
| `INFLUXDB_BUCKET_ANALYTICS_15MIN` | `analytics_15min` | New only | 15-min analytics |
| `INFLUXDB_BUCKET_ANALYTICS_1HOUR` | `analytics_1hour` | New only | 1-hour analytics |
| `INFLUXDB_BUCKET_WINDPOWER` | `windpower` | New only | Wind power production/forecasts |
| `INFLUXDB_BUCKET_LOAD_CONTROL` | `load_control` | New only | Heating program schedules |

For integration tests append `_test` to each bucket name and create them as
described in the README.
