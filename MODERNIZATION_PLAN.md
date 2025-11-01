# Red House - Home Automation Modernization Plan

**Project Name:** `redhouse`
**Started:** 2025-10-18
**Current Phase:** Phase 4

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

- ✅ Version control with Git/GitHub
- ✅ Configuration management (no hardcoded credentials)
- ⏳ Deployment automation
- ⏳ Unit tests
- ⏳ Simulation capability (backtest against historical data)
- ⏳ Automatic log rotation
- ⏳ Monitoring and alerts
- ⏳ Extensibility for garage heating and EV charging
- ⏳ Grafana dashboard controls

---

## Technology Stack Decisions

### Raspberry Pi Stack
- **Python 3.9+** with virtual environment (venv)
- **systemd services + timers** (replacing crontab)
- **Structured logging** with rotation
- **Environment variables** for secrets
- **Git deployment** via simple script

### NOT Using
- ❌ Docker on Pi (unnecessary overhead)
- ❌ Complex message queues
- ❌ Microservices architecture

### NAS Stack (Keep As-Is)
- ✅ InfluxDB 2.x (Docker)
- ✅ Grafana (Docker)
- Action items: Add backup script, verify retention policies

---

## Progress: Phase 1 - Repository Setup & Code Organization

### ✅ COMPLETED

1. **Project Structure Created**
```
redhouse/
├── .gitignore                    ✅ Created
├── .env.example                  ✅ Created
├── README.md                     ✅ Created
├── requirements.txt              ✅ Created
├── config/
│   └── config.yaml.example       ✅ Created
├── src/
│   ├── __init__.py              ✅ Created
│   ├── common/
│   │   ├── __init__.py          ✅ Created
│   │   ├── config.py            ✅ Created
│   │   ├── logger.py            ✅ Created
│   │   └── influx_client.py     ✅ Created
│   ├── data_collection/
│   │   └── __init__.py          ✅ Created
│   ├── control/
│   │   └── __init__.py          ✅ Created
│   └── simulation/
│       └── __init__.py          ✅ Created
├── tests/
│   ├── unit/                    ✅ Created
│   └── integration/             ✅ Created
├── deployment/
│   └── systemd/                 ✅ Created
└── grafana/
    └── dashboards/              ✅ Created
```

2. **Core Infrastructure Modules**
   - ✅ `src/common/config.py` - Configuration loader (env vars + YAML)
   - ✅ `src/common/logger.py` - Structured logging with rotation
   - ✅ `src/common/influx_client.py` - InfluxDB client wrapper

3. **Configuration Files**
   - ✅ `.gitignore` - Excludes credentials, logs, old backups
   - ✅ `.env.example` - Template for environment variables
   - ✅ `config.yaml.example` - System configuration template
   - ✅ `requirements.txt` - Python dependencies

4. **Documentation**
   - ✅ `README.md` - Comprehensive setup and usage guide

### ✅ COMPLETED

Phase 1 is now 100% complete with git repository initialized and pushed to GitHub!

---

## Phase 2 - Refactor Existing Code (Week 1-2)

**Current Status:** ✅ 100% COMPLETE! All 4 modules refactored!

### Priority: Refactor data collection modules

**Order of refactoring:**
1. ✅ Temperature collection (wibatemp.py → src/data_collection/temperature.py)
2. ✅ Weather data (get_weather.py → src/data_collection/weather.py)
3. ✅ Spot prices (spot_price_getter.py → src/data_collection/spot_prices.py)
4. ✅ CheckWatt data (checkwatt_dataloader.py → src/data_collection/checkwatt.py)

**Refactoring Checklist for Each Module:**
- [x] Remove hardcoded credentials (use config)
- [x] Replace print statements with logging
- [x] Add type hints
- [x] Extract reusable functions
- [x] Use InfluxClient wrapper
- [x] Add docstrings
- [x] Keep backwards compatibility during transition

### ✅ Temperature Collection - COMPLETE

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

### ✅ Weather Data Collection - COMPLETE

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

### ✅ Spot Prices Collection - COMPLETE

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

### ✅ CheckWatt Data Collection - COMPLETE

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

**Current Status:** ✅ 100% COMPLETE!

### ✅ Unit Tests - COMPLETE
- [x] Test configuration loading (13 tests, 11 passing, 2 skipped)
- [x] Test configuration validation (14 tests)
- [x] Test temperature collection (10 tests)
- [x] Test weather data collection (8 tests)
- [x] Test spot price collection (9 tests)
- [x] Test CheckWatt data collection (9 tests)
- **Total: 64 unit tests passing, 2 skipped**

### ✅ Integration Tests - COMPLETE
- [x] Test InfluxDB read/write
- [x] Test safety system (blocks test data in production)
- [x] Test end-to-end data collection
- **Total: 4 integration tests**

### ✅ Test Infrastructure - COMPLETE
- [x] Create pytest.ini configuration
- [x] Configure test markers (unit, integration, slow)
- [x] Set up test fixtures and mocking
- [x] Async test support configured

### ✅ Code Quality Tools - COMPLETE
- [x] Add black formatter (line length 100)
- [x] Add ruff linter (96 issues auto-fixed)
- [x] Create pyproject.toml configuration
- [x] Format all source and test files
- [x] Update deprecated type hints (Dict→dict, List→list)
- [x] Remove unused imports

### ✅ CI/CD Pipeline - COMPLETE
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

**Current Status:** 🚧 50% Complete - Heating Curve & Optimizer Done

### Overview
Refactor the core heating control system that optimizes when to heat based on weather forecasts, electricity prices, and solar production.

**Existing Code to Refactor:**
- `wibatemp/generate_heating_program.py` (407 lines) - Daily heating schedule optimizer
- `wibatemp/execute_heating_program.py` (98 lines) - Schedule executor
- `wibatemp/mlp_control.sh` - I2C heat pump controller

### Phase 4.1 - Heating Curve & Calculations ✅ COMPLETED
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

### Phase 4.2 - Program Generator
**Goal:** Generate daily heating schedules

- [ ] Create `src/control/program_generator.py`
  - Fetch weather, spot price, and solar forecast data
  - Calculate required heating hours from temperature
  - Generate quarterhourly schedule (ON/ALE/EVU states)
  - Save schedule as JSON
  - Also save the schedule to the InfluxDB (design a proper database format for this for it to be easily plotted in Grafana)
  - Unit tests with fixture data
  - Integration test with test InfluxDB

- [ ] Create `generate_heating_program_v2.py` wrapper
  - Backward compatibility with cron
  - Command-line arguments (--date, --dry-run, --output)
  - Logging instead of print statements

### Phase 4.3 - Program Executor
**Goal:** Execute heating schedules safely

- [ ] Create `src/control/program_executor.py`
  - Load daily schedule JSON
  - Execute commands at scheduled times
  - Call pump controller (I2C via mlp_control.sh)
  - Mark commands as executed
  - Handle day transitions
  - Unit tests with mocked I2C calls
  - Dry-run mode for testing

- [ ] Create `execute_heating_program_v2.py` wrapper
  - Backward compatibility with cron
  - Safety checks before pump control
  - Error handling and logging
  - Status reporting to InfluxDB

### Phase 4.4 - Pump Controller Wrapper
**Goal:** Safe I2C pump control

- [ ] Create `src/control/pump_controller.py`
  - Python wrapper for mlp_control.sh
  - I2C communication via smbus or subprocess
  - Safety interlocks (temperature limits, max runtime)
  - Status monitoring
  - Mock mode for testing
  - Unit tests without hardware

### Phase 4.5 - Testing
**Goal:** Comprehensive test coverage

- [ ] Unit tests (20+ tests expected):
  - Test heating curve calculations
  - Test priority calculations
  - Test schedule generation
  - Test schedule execution logic
  - Test pump controller (mocked I2C)

- [ ] Integration tests:
  - End-to-end schedule generation
  - Schedule execution in dry-run mode
  - Data fetching from test InfluxDB

- [ ] Simulation tests:
  - Test with historical data
  - Verify cost optimization
  - Validate heating hour distribution

### Success Criteria
- ✅ All heating logic extracted from old scripts
- ✅ Configurable heating curve (no hardcoded values)
- ✅ Comprehensive unit tests (>20 tests)
- ✅ Dry-run mode for safe testing
- ✅ Backward-compatible wrappers for cron
- ✅ All tests passing

---

## Phase 5 - Deployment Automation (Week 4-5)

### systemd Services

**Services to create:**
1. `redhouse-temperature.service` + `.timer` (every minute)
2. `redhouse-weather.service` + `.timer` (hourly)
3. `redhouse-spot-prices.service` + `.timer` (daily at specific times)
4. `redhouse-checkwatt.service` + `.timer` (every 5 minutes)
5. `redhouse-generate-program.service` + `.timer` (daily at 16:05)
6. `redhouse-execute-program.service` + `.timer` (every 15 min)

### Deployment Script

```bash
#!/bin/bash
# deployment/deploy.sh

set -e

DEPLOY_DIR=/opt/redhouse
REPO_URL=https://github.com/<username>/redhouse.git

cd $DEPLOY_DIR
git pull origin main

source venv/bin/activate
pip install -r requirements.txt

pytest tests/

sudo systemctl daemon-reload
sudo systemctl restart redhouse-*

echo "Deployment complete!"
```

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
