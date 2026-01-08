# Red House - Home Automation Modernization Plan

**Project Name:** `redhouse`
**Started:** 2025-10-18
**Current Phase:** Phase 6 (Phases 4-5 Complete)

---

## Project Overview

Modernizing a home automation system running on Raspberry Pi that controls a geothermal heat pump based on weather forecasts and electricity spot prices. The system uses InfluxDB (on NAS) for storage and Grafana for visualization.

### Current System Components

**Data Collection:**
- Temperature sensors (1-wire DS18B20) via wibatemp.py
- Weather forecast via FMI API (get_weather.py)
- Spot electricity prices (spot_price_getter.py)
- Battery/solar data from CheckWatt (checkwatt_dataloader.py)
- Solar yield predictions (predict_solar_yield.py)
- Wind power data (windpowergetter.py)
- Ping monitoring (pinglogger.py)

**Control Logic:**
- Daily heating program generation (generate_heating_program.py)
- Heating program execution every 15 min (execute_heating_program.py)
- Geothermal pump control via I2C (mlp_control.sh)

**Storage & Visualization:**
- InfluxDB 2.x on Asustor NAS (192.168.1.164:8086)
- Grafana on same NAS
- Both running under Docker

---

## Goals

- [DONE] Version control with Git/GitHub
- [DONE] Configuration management (no hardcoded credentials)
- [TODO] Deployment automation
- [TODO] Unit tests
- [TODO] Simulation capability (backtest against historical data)
- [TODO] Automatic log rotation
- [TODO] Monitoring and alerts
- [TODO] Extensibility for garage heating and EV charging
- [TODO] Grafana dashboard controls

---

## Technology Stack Decisions

### Raspberry Pi Stack
- **Python 3.9+** with virtual environment (venv)
- **systemd services + timers** (replacing crontab)
- **Structured logging** with rotation
- **Environment variables** for secrets
- **Git deployment** via simple script

### NOT Using
- [NO] Docker on Pi (unnecessary overhead)
- [NO] Complex message queues
- [NO] Microservices architecture

### NAS Stack (Keep As-Is)
- [DONE] InfluxDB 2.x (Docker)
- [DONE] Grafana (Docker)
- Action items: Add backup script, verify retention policies

---

## Progress: Phase 1 - Repository Setup & Code Organization

### [DONE] COMPLETED

1. **Project Structure Created**
```
redhouse/
├── .gitignore                    [DONE] Created
├── .env.example                  [DONE] Created
├── README.md                     [DONE] Created
├── requirements.txt              [DONE] Created
├── config/
│   └── config.yaml.example       [DONE] Created
├── src/
│   ├── __init__.py              [DONE] Created
│   ├── common/
│   │   ├── __init__.py          [DONE] Created
│   │   ├── config.py            [DONE] Created
│   │   ├── logger.py            [DONE] Created
│   │   └── influx_client.py     [DONE] Created
│   ├── data_collection/
│   │   └── __init__.py          [DONE] Created
│   ├── control/
│   │   └── __init__.py          [DONE] Created
│   └── simulation/
│       └── __init__.py          [DONE] Created
├── tests/
│   ├── unit/                    [DONE] Created
│   └── integration/             [DONE] Created
├── deployment/
│   └── systemd/                 [DONE] Created
└── grafana/
    └── dashboards/              [DONE] Created
```

2. **Core Infrastructure Modules**
   - [DONE] `src/common/config.py` - Configuration loader (env vars + YAML)
   - [DONE] `src/common/logger.py` - Structured logging with rotation
   - [DONE] `src/common/influx_client.py` - InfluxDB client wrapper

3. **Configuration Files**
   - [DONE] `.gitignore` - Excludes credentials, logs, old backups
   - [DONE] `.env.example` - Template for environment variables
   - [DONE] `config.yaml.example` - System configuration template
   - [DONE] `requirements.txt` - Python dependencies

4. **Documentation**
   - [DONE] `README.md` - Comprehensive setup and usage guide

### [DONE] COMPLETED

Phase 1 is now 100% complete with git repository initialized and pushed to GitHub!

---

## Phase 2 - Refactor Existing Code (Week 1-2)

**Current Status:** [DONE] 100% COMPLETE! All 4 modules refactored!

### Priority: Refactor data collection modules

**Order of refactoring:**
1. [DONE] Temperature collection (wibatemp.py -> src/data_collection/temperature.py)
2. [DONE] Weather data (get_weather.py -> src/data_collection/weather.py)
3. [DONE] Spot prices (spot_price_getter.py -> src/data_collection/spot_prices.py)
4. [DONE] CheckWatt data (checkwatt_dataloader.py -> src/data_collection/checkwatt.py)

**Refactoring Checklist for Each Module:**
- [x] Remove hardcoded credentials (use config)
- [x] Replace print statements with logging
- [x] Add type hints
- [x] Extract reusable functions
- [x] Use InfluxClient wrapper
- [x] Add docstrings
- [x] Keep backwards compatibility during transition

### [DONE] Temperature Collection - COMPLETE

**Completed:**
- [x] Refactored [src/data_collection/temperature.py](src/data_collection/temperature.py)
- [x] Added type hints and comprehensive docstrings
- [x] Removed all hardcoded credentials
- [x] Implemented structured logging
- [x] Created 10 unit tests (all passing)
- [x] Added --dry-run and --verbose flags
- [x] Created [collect_temperatures.py](collect_temperatures.py) wrapper
- [x] Added integration tests
- [x] Created test bucket infrastructure
- [x] Verified writes to InfluxDB test bucket

**Testing:**
```bash
# Unit tests (safe, no hardware/DB access)
pytest tests/unit/test_temperature.py -v

# Integration tests (writes to test bucket)
python tests/integration/test_influx_connection.py

# Dry-run on actual hardware
python collect_temperatures.py --dry-run --verbose
```

### [DONE] Weather Data Collection - COMPLETE

**Completed:**
- [x] Refactored [src/data_collection/weather.py](src/data_collection/weather.py)
- [x] Added type hints and comprehensive docstrings
- [x] Removed all hardcoded credentials and location data
- [x] Implemented structured logging
- [x] Created 8 unit tests (all passing)
- [x] Added --dry-run, --verbose, and --save-file flags
- [x] Created [collect_weather.py](collect_weather.py) wrapper
- [x] Integrated with FMI API for weather forecasts
- [x] Fetches 200 forecast points (15-min intervals, ~50 hours)

**Testing:**
```bash
# Unit tests (safe, mocked API calls)
pytest tests/unit/test_weather.py -v

# Dry-run (makes real API call, doesn't write to DB)
python collect_weather.py --dry-run --verbose

# With file backup
python collect_weather.py --dry-run --save-file
```

### [DONE] Spot Prices Collection - COMPLETE

**Completed:**
- [x] Refactored [src/data_collection/spot_prices.py](src/data_collection/spot_prices.py)
- [x] Added type hints and comprehensive docstrings
- [x] Removed all hardcoded credentials
- [x] Implemented structured logging
- [x] Created 9 unit tests (all passing)
- [x] Added --dry-run and --verbose flags
- [x] Created [collect_spot_prices.py](collect_spot_prices.py) wrapper
- [x] Fetches 192 quarter-hourly prices (15-min intervals, 48 hours)
- [x] Calculates final prices with VAT, margins, and transfer costs
- [x] Supports day/night transfer pricing

**Testing:**
```bash
# Unit tests (safe, mocked API calls)
pytest tests/unit/test_spot_prices.py -v

# Dry-run (makes real API call, doesn't write to DB)
python collect_spot_prices.py --dry-run
```

### [DONE] CheckWatt Data Collection - COMPLETE

**Completed:**
- [x] Refactored [src/data_collection/checkwatt.py](src/data_collection/checkwatt.py)
- [x] Added type hints and comprehensive docstrings
- [x] Removed all hardcoded credentials (including Basic auth!)
- [x] Implemented structured logging
- [x] Created 9 unit tests (all passing)
- [x] Added --dry-run, --verbose, --last-hour flags
- [x] Created [collect_checkwatt.py](collect_checkwatt.py) wrapper
- [x] Fetches 6 data streams: Battery SoC, Charge, Discharge, Import, Export, Solar
- [x] 1-minute interval data points
- [x] Respectful API usage (--last-hour by default)

**Testing:**
```bash
# Unit tests (safe, mocked API calls)
pytest tests/unit/test_checkwatt.py -v

# Dry-run (makes real API call, doesn't write to DB)
python collect_checkwatt.py --dry-run --last-hour
```

**⚠️ Important Note:**
CheckWatt data is fetched from non-public internal APIs. Monitor for API changes:
- [ ] **TODO**: Add alert if CheckWatt API response format changes
- [ ] **TODO**: Add retry logic with exponential backoff
- [ ] **TODO**: Consider rate limiting to be extra respectful

### Example: Temperature Collection Refactor

```python
# src/data_collection/temperature.py
from src.common.config import get_config
from src.common.logger import setup_logger
from src.common.influx_client import InfluxClient

logger = setup_logger(__name__, 'temperature.log')

def collect_temperatures():
    config = get_config()
    influx = InfluxClient(config)

    # Use existing logic from wibatemp.py
    temp_status = get_temperature_status()

    # Write using client wrapper
    success = influx.write_temperatures(temp_status)

    if success:
        logger.info(f"Collected {len(temp_status)} temperatures")
    else:
        logger.error("Failed to write temperatures")
```

---

## Phase 3 - Testing Infrastructure

**Current Status:** [DONE] 100% COMPLETE!

### [DONE] Unit Tests - COMPLETE
- [x] Test configuration loading (13 tests, 11 passing, 2 skipped)
- [x] Test configuration validation (14 tests)
- [x] Test temperature collection (10 tests)
- [x] Test weather data collection (8 tests)
- [x] Test spot price collection (9 tests)
- [x] Test CheckWatt data collection (9 tests)
- **Total: 64 unit tests passing, 2 skipped**

### [DONE] Integration Tests - COMPLETE
- [x] Test InfluxDB read/write
- [x] Test safety system (blocks test data in production)
- [x] Test end-to-end data collection
- [x] Aggregation pipeline integration tests (tests/integration/)
  - Test 5-min aggregator writes with measurement="energy"
  - Test 15-min aggregator reads from 5-min bucket
  - Test 1-hour aggregator reads from 5-min bucket
  - Test analytics cost calculations
  - All tests use *_test buckets only
  - Run only during development, not at deployment
- **Total: 8 integration tests**

### [DONE] Test Infrastructure - COMPLETE
- [x] Create pytest.ini configuration
- [x] Configure test markers (unit, integration, slow)
- [x] Set up test fixtures and mocking
- [x] Async test support configured

### DONE Code Quality Tools - COMPLETE
- [x] Add black formatter (line length 100)
- [x] Add ruff linter (96 issues auto-fixed)
- [x] Create pyproject.toml configuration
- [x] Format all source and test files
- [x] Update deprecated type hints (Dict→dict, List→list)
- [x] Remove unused imports

### DONE CI/CD Pipeline - COMPLETE
- [x] Create GitHub Actions workflow (.github/workflows/tests.yml)
- [x] Test on Python 3.9, 3.10, 3.11
- [x] Run pytest on every push/PR
- [x] Run black and ruff checks
- [x] Integration tests allowed to fail (require InfluxDB access)

**Testing Summary:**
```bash
# Run all tests
pytest tests/ -v

# Results: 64 passed, 2 skipped
# Coverage: All data collection modules + core infrastructure
```

**Code Quality:**
```bash
# Format code
black src/ tests/

# Lint code
ruff check src/ tests/

# All checks passing in CI
```

---

## Phase 4 - Heating Control Logic (Week 3-4)

**Current Status:** DONE 100% Complete - All Components Implemented & Tested

### Overview
Refactor the core heating control system that optimizes when to heat based on weather forecasts, electricity prices, and solar production.

**Existing Code to Refactor:**
- `wibatemp/generate_heating_program.py` (407 lines) - Daily heating schedule optimizer
- `wibatemp/execute_heating_program.py` (98 lines) - Schedule executor
- `wibatemp/mlp_control.sh` - I2C heat pump controller

### Phase 4.1 - Heating Curve & Calculations DONE COMPLETED
**Goal:** Extract and test the heating curve logic

- [x] Create `src/control/heating_curve.py`
  - Extract heating curve function (temp → hours/day)
  - Support configurable curve points from YAML
  - Linear interpolation between points
  - Unit tests for all temperature ranges (18 tests, all passing)

- [x] Create `src/control/heating_data_fetcher.py`
  - Fetch weather, spot prices, solar predictions from InfluxDB
  - Merge data into pandas DataFrame for analysis
  - Calculate day average temperatures

- [x] Create `src/control/heating_optimizer.py`
  - Calculate heating priorities from spot prices + solar
  - Optimize heating hours to cheapest electricity periods
  - Support both hourly (60min) and quarterly (15min) resolutions
  - Ready for future quarterly-hour billing
  - Unit tests with mocked data (20 tests, all passing)

### Phase 4.2 - Program Generator DONE COMPLETED
**Goal:** Generate daily heating schedules

- [x] Create `src/control/program_generator.py` (692 lines)
  - Fetch weather, spot price, and solar forecast data
  - Calculate required heating hours from temperature
  - Generate hourly schedule with EVU-OFF optimization
  - Multi-load support (geothermal pump, garage heater, EV charger)
  - Save schedule as JSON with full metadata
  - Save schedule to InfluxDB (load_control bucket with data_type tag)
  - Simulation mode for backtesting
  - Unit tests with fixture data (15 tests, all passing)

- [x] Create `generate_heating_program_v2.py` wrapper (175 lines)
  - Command-line arguments (--date-offset, --dry-run, --simulation, --base-date)
  - Comprehensive logging
  - Error handling and validation

### Phase 4.3 - Program Executor DONE COMPLETED
**Goal:** Execute heating schedules safely

- [x] Create `src/control/program_executor.py` (370 lines)
  - Load daily schedule JSON (v2.0 format)
  - Execute commands at scheduled times
  - Call pump controller for geothermal pump
  - Mark commands as executed in JSON
  - Handle day transitions (merge yesterday's unexecuted)
  - Write actual execution to InfluxDB (data_type="actual")
  - Unit tests with mocked controller
  - Dry-run mode for testing

- [x] Create `execute_heating_program_v2.py` wrapper (164 lines)
  - Command-line arguments (--dry-run, --force, --date)
  - Safety checks and validation
  - Comprehensive error handling and logging
  - Status reporting to InfluxDB

### Phase 4.4 - Pump Controller Wrapper DONE COMPLETED
**Goal:** Safe I2C pump control

- [x] Create `src/control/pump_controller.py` (240 lines)
  - Python wrapper for mlp_control.sh
  - PumpController class for geothermal pump (I2C via subprocess)
  - MultiLoadController for future loads (garage, EV)
  - Valid commands: ON, ALE, EVU
  - Execution delay tracking
  - Dry-run mode for testing
  - Unit tests without hardware (12 tests, all passing)

### Phase 4.5 - Testing DONE COMPLETED
**Goal:** Comprehensive test coverage

- [x] Unit tests (65 total):
  - Heating curve calculations (18 tests)
  - Priority calculations (20 tests)
  - Schedule generation (15 tests)
  - Pump controller (12 tests)
  - All tests passing

- [x] Integration tests (6 tests):
  - InfluxDB connection test
  - Data fetcher test
  - End-to-end schedule generation
  - InfluxDB save test
  - Schedule execution in dry-run mode
  - Full simulation test
  - Test suite created and documented

### Success Criteria
- DONE All heating logic extracted from old scripts
- DONE Configurable heating curve (no hardcoded values)
- DONE Comprehensive unit tests (65 unit + 6 integration tests)
- DONE Dry-run mode for safe testing
- DONE Multi-load architecture (geothermal, garage, EV)
- DONE Simulation mode for backtesting
- DONE All tests passing
- DONE CI/CD pipeline passing (black + ruff + pytest)

**Git Commits:**
- c84edd8 - Complete Phase 4: Heating Control Logic Implementation
- ab5c074 - Add Phase 4 integration tests and fix bugs
- 9f912bb - Fix linting errors in Phase 4 code

**Test Summary:**
- 137 unit tests passing (2 skipped)
- 6 integration tests created
- All black formatting passing
- All ruff linting passing

---

## Phase 5 - Deployment Automation (Week 4-5)

**Current Status:** [DONE] 100% Complete - systemd services created

### systemd Services [DONE]

**Services created:**
1. [DONE] `redhouse-temperature.service` + `.timer` (every minute)
2. [DONE] `redhouse-weather.service` + `.timer` (hourly at :02)
3. [DONE] `redhouse-spot-prices.service` + `.timer` (6 times daily 13:29-15:59)
4. [DONE] `redhouse-checkwatt.service` + `.timer` (every 5 min at :01, :06, :11...)
5. [DONE] `redhouse-solar-prediction.service` + `.timer` (hourly at :03)
6. [DONE] `redhouse-generate-program.service` + `.timer` (daily at 16:05)
7. [DONE] `redhouse-execute-program.service` + `.timer` (every 15 min)
8. [DONE] `redhouse-evu-cycle.service` + `.timer` (every 2 hours at :23)

### Deployment Script [DONE]

- [DONE] `deployment/deploy.sh` - Automated deployment script
  - Pull latest code from GitHub
  - Update Python dependencies
  - Run unit tests before deployment
  - Install/update systemd services
  - Restart all timers
  - Full error handling and status reporting

### Documentation [DONE]

- [DONE] `deployment/README.md` - Comprehensive deployment guide
  - Service descriptions and schedules
  - Installation and update procedures
  - Manual service management
  - Log viewing with journalctl
  - Monitoring and troubleshooting
  - Migration guide from crontab

### Benefits Over Crontab

- [DONE] Better logging via journalctl (structured, persistent, searchable)
- [DONE] Service dependency management
- [DONE] Automatic restart on failure
- [DONE] Easier monitoring and debugging
- [DONE] Unit-based organization
- [DONE] Boot-time service ordering

### Success Criteria

- [DONE] All data collection services automated
- [DONE] All heating control services automated
- [DONE] Deployment script tested
- [DONE] Documentation complete
- [DONE] Ready for Raspberry Pi deployment

**Git Commit:**
- 80e90b4 - Add Phase 5: Deployment Automation with systemd services

**Future Enhancement:**
- [ ] Smart EVU-OFF cycling based on actual pump ON time (not fixed schedule)
- [ ] Health check service monitoring data freshness
- [ ] Automated alerts on service failures

---

## Phase 6 - Log Management (Week 5-6)

1. **Replace remaining print statements with logging** (heating control scripts)
2. **Configure log rotation** (10MB, 5 backups) - already done
3. **Add logrotate config** for /var/log/redhouse/
4. **Centralize logs** in /var/log/redhouse/

---

## Phase 7 - Monitoring & Alerts (Week 6-7)

### Health Checks
- Temperature sensors responding
- Data freshness in InfluxDB
- Heating program generated for tomorrow
- Pump control successful

### Alerting
- Email notifications
- Grafana alert rules
- Log critical errors

---

## Phase 8 - Historical Simulation (Week 7-8)

**Backtesting Framework:**
```python
sim = HeatingSimulator("2024-01-01", "2024-12-31")
results = sim.compare_strategies({
    "current": CurrentStrategy(),
    "optimized": NewStrategy()
})
print(f"Potential savings: {results['savings']} EUR/year")
```

---

## Phase 9 - Multi-Load Support (Week 8-9)

### Load Balancer
- Geothermal pump (priority 1, 3kW)
- Garage heater (priority 2, 2kW)
- EV charger (priority 3, 11kW)
- Max power limit: 25kW

---

## Phase 10 - Grafana Controls (Week 9-10)

### REST API
```python
# Flask API for control
POST /api/heating/override {"mode": "ON", "duration": 2}
POST /api/garage/boost {"duration": 1}
GET /api/status
```

### Grafana Button Panels
- Boost heating
- Eco mode
- Heat garage
- Manual overrides

---

## Quick Reference

### Important: Always Activate Virtual Environment
**REMINDER:** Before running any Python commands, tests, or scripts:
```bash
# On Windows (Git Bash)
source venv/Scripts/activate

# On Linux/Raspberry Pi
source venv/bin/activate
```

### Key Files Locations (Current/Old System)
- Temperature: `wibatemp/wibatemp.py`
- Weather: `wibatemp/get_weather.py`
- Spot prices: `wibatemp/spot_price_getter/spot_price_getter.py`
- Heating optimizer: `wibatemp/generate_heating_program.py`
- Heating executor: `wibatemp/execute_heating_program.py`
- Solar yield predictor: `wibatemp/predict_solar_yield.py`
- Water temp controller `wibatemp/water_temp_controller.py`
- Pinglogger `pinglogger/pinglogger.py`
- Pump control: `wibatemp/mlp_control.sh`
- Pump EVUcycleoff script: `wibatemp/mlp_cycle_evu_off_hourly.sh`
  (It is needed to cycle the EVUOFF once per two hours for the pump not to go to direct heating mode)
- Crontab: `crontab.list`

### InfluxDB Buckets
- `temperatures` - Temperature and humidity data
- `weather` - Weather forecasts
- `spotprice` - Electricity prices
- `emeters` - Energy meter data
- `checkwatt_full_data` - Battery/solar data

### Hardware
- **Temperature sensors:** 1-wire DS18B20 at `/sys/bus/w1/devices/`
- **Pump control:** I2C bus 1, address 0x10
- **Shelly relay:** HTTP at 192.168.1.5
(There are multiple Shelly relays, two for Garage heating (different size), one for AC, one for hot water pump circulation, potentially several for some AC boosting in Movie room etc.)

---

## Notes & Decisions

1. **Removed pysma dependency** - No longer have SMA inverter
2. **Local folder name:** `redhouse` locally, GitHub repo is `redhouse`
3. **InfluxDB/Grafana on NAS:** Keep as-is, just add backups
4. **No Docker on Pi:** Too much overhead for Python scripts
5. **systemd over crontab:** Better logging and service management
6. **NO UNICODE CHARACTERS** - Always use ASCII equivalents only
   - Use [OK], [FAIL], [WARN], [SKIP] instead of checkmarks/symbols
   - Ensures compatibility with all terminals and log files
   - Prevents encoding issues on Raspberry Pi

---

## Contact & Resources

- **FMI Weather API:** https://en.ilmatieteenlaitos.fi/open-data
- **Spot Price API:** https://api.spot-hinta.fi/
- **InfluxDB Docs:** https://docs.influxdata.com/
- **systemd Timers:** https://www.freedesktop.org/software/systemd/man/systemd.timer.html

---
