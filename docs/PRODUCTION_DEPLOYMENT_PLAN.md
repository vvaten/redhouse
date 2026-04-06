# RedHouse Production Deployment Plan

**Created:** 2026-04-06
**Status:** In Progress
**Goal:** Replace wibatemp with redhouse as the production system

---

## Overview

Migrate from wibatemp (crontab-based) to redhouse (systemd-based) using a
gradual feature-by-feature cutover. Both systems write to the same InfluxDB
buckets in the same format, so there should be zero data gaps.

## Infrastructure Layout

```
Raspberry Pi
  /opt/redhouse/            -- Production (STAGING_MODE=false)
    .env                    -- Production secrets + bucket names
    config/config.yaml      -- Shared app config (from git)
    config/sensors.yaml     -- Sensor mapping (PII, not in git)
    venv/                   -- Python virtual environment
    systemd timers          -- Enabled, running

  /opt/redhouse-staging/    -- Staging (STAGING_MODE=true)
    .env                    -- Staging secrets + *_staging bucket names
    config/config.yaml      -- Same as production (from git)
    config/sensors.yaml     -- Same as production (copy)
    venv/                   -- Separate virtual environment
    systemd timers          -- NOT enabled by default, run manually

NAS (192.168.1.164)
  InfluxDB                  -- Production + staging buckets
  Grafana
    Production dashboard    -- Queries production buckets
    Staging dashboard       -- Queries *_staging buckets
```

---

## Pre-requisites (before any migration steps)

- [x] config.yaml mandatory, silent defaults removed
- [x] EVU-OFF threshold bug fixed (0.40 EUR/kWh, matches wibatemp)
- [x] sensors.yaml created on Pi with all 17 sensor IDs
- [x] Staging running for several weeks, issues fixed
- [x] Fix humidity gap -- redhouse temperature collector writes
  humidity data (wibatemp writes both temperatures and humidities)
- [x] Create production deploy script (deployment/deploy_production.sh)
- [x] Create staging deploy script (deployment/deploy_staging.sh)
- [x] Create production Grafana dashboard + deploy script
- [x] Verify config.yaml loads correctly on Pi (curve, EVU, sensors)
- [ ] Set up staging environment /opt/redhouse-staging (Step 1)
- [ ] Hand off staging to /opt/redhouse-staging (Step 2)
- [ ] Switch /opt/redhouse to production mode (Step 3)
- [ ] Pump control test (Step 4)
- [ ] Temperature collection test - 24h (Step 5)
- [ ] Create staging data copy timer (daily production -> staging)

---

## Deployment Scripts

### Production Deploy (deployment/deploy_production.sh)

```
Target:    /opt/redhouse
Branch:    main (always)
Run as:    sudo deployment/deploy_production.sh
```

Assumes code has already been tested in staging before production deploy.

Steps (wait for safe window at :07, :22, :37, :52):
  1. git pull origin main
  2. pip install -r requirements.txt (if dependencies changed)
  3. Validate .env and config/sensors.yaml exist
  4. Install systemd service + timer files to /etc/systemd/system/
  5. daemon-reload, enable and restart all production timers
  6. Set up Grafana production alerts
  7. Show timer status

Does NOT:
  - Touch /opt/redhouse-staging
  - Modify .env (manual only)
  - Deploy from non-main branches

### Staging Deploy (deployment/deploy_staging.sh)

```
Target:    /opt/redhouse-staging
Branch:    any (argument, defaults to main)
Run as:    sudo deployment/deploy_staging.sh [branch-name]
```

Steps:
  1. git fetch, git checkout <branch>
  2. Create/update venv, pip install -r requirements.txt
  3. Validate .env exists (staging bucket names)
  4. Validate config/sensors.yaml exists
  5. Run unit tests (abort on failure)
  6. Copy latest production data to staging buckets (top-up)
  7. Print instructions for manual testing

Does NOT:
  - Enable systemd timers by default (can be enabled manually when needed)
  - Set up Grafana alerts
  - Touch /opt/redhouse (production)
  - Modify .env (manual only)

### Staging Data Refresh

Staging needs fresh production data to process. Two mechanisms:

1. **Daily automatic copy** (cron/timer on Pi):
   ```bash
   # Copies last 2 days of production data to staging buckets
   cd /opt/redhouse-staging
   venv/bin/python deployment/copy_production_to_staging.py --days 2
   ```
   Runs daily (e.g., 04:00 after backups complete).

2. **On-demand top-up** (staging deploy script does this automatically)

### Usage

```bash
# Deploy latest main to production
sudo /opt/redhouse/deployment/deploy_production.sh

# Deploy a feature branch to staging for testing
sudo /opt/redhouse/deployment/deploy_staging.sh feature/my-change

# Deploy main to staging (e.g., to verify before production deploy)
sudo /opt/redhouse/deployment/deploy_staging.sh

# Test a specific collector in staging
cd /opt/redhouse-staging
source venv/bin/activate
python collect_temperatures.py --dry-run --verbose

# Test heating program generation against fresh production data
python generate_heating_program_v2.py --dry-run
```

---

## Grafana Dashboards

- **Production**: "RedHouse Energy Monitor" -- queries production buckets
- **Staging**: "RedHouse Energy Monitor (Staging)" -- queries *_staging buckets
- Both dashboards have identical panels, only bucket names differ

### Workflow for dashboard changes

1. Edit the staging dashboard in Grafana (safe to experiment)
2. Verify panels work with staging data
3. Export staging dashboard JSON
4. Replace bucket names (*_staging -> production) and promote:
   - Save as production dashboard JSON
   - Import to Grafana as production dashboard
5. Commit both JSON files to git

Scripts:
- `deployment/clone_grafana_dashboard_to_staging.py` -- production -> staging
- TODO: `deployment/promote_staging_dashboard.py` -- staging -> production

---

## Pump Control Test (pre-requisite)

Redhouse has never controlled the real pump. Before the gradual migration,
do a controlled test to verify I2C communication works.

NOTE: control_pump.py does not write to InfluxDB, so no bucket name
changes are needed. But STAGING_MODE must be set to false temporarily
because PumpController uses mock hardware when staging mode is on.

### Preparation

1. Choose a safe time (daytime, mild weather, not during EVU-OFF)
2. Note current pump state:
   ```bash
   cat /home/pi/wibatemp/mlp_status.txt
   ```
3. Verify I2C bus is working:
   ```bash
   i2cdetect -y 1
   # Should show device at address 0x10
   ```

### Test procedure

```bash
# 1. Stop wibatemp heating control (keep other cron jobs running)
crontab -e
# Temporarily comment out:
#   Line 34: execute_heating_program (*/15)
#   Line 35: mlp_cycle_evu_off (*/2 :23)
# Do NOT comment out line 33 (generate) - it only runs at 16:05

# 2. Temporarily disable staging mode (for real I2C access)
cd /opt/redhouse
sudo -u pi nano .env
# Change: STAGING_MODE=false
# Do NOT change bucket names (pump control doesn't write to InfluxDB)

# 3. Test pump ON command
source venv/bin/activate
python control_pump.py ON --verbose
# Verify: pump turns on, no I2C errors

# 4. Test pump ALE command (lower temperature mode)
python control_pump.py ALE --verbose
# Verify: pump switches mode

# 5. Test pump ON again (restore normal heating)
python control_pump.py ON --verbose

# 6. Test EVU cycle
python control_pump.py EVU --verbose
sleep 30
python control_pump.py ON --verbose
# Verify: pump goes to EVU-OFF and back

# 7. Restore staging mode
sudo -u pi nano .env
# Change: STAGING_MODE=true

# 8. Restore wibatemp heating control
crontab -e
# Uncomment lines 34 and 35

# 9. Verify wibatemp resumes
# Wait for next */15 minute mark
tail -5 /home/pi/wibatemp/execute_heating_program.log
```

### Expected results

- Each pump command completes without I2C errors
- Pump physically responds (listen for relay click or check pump display)
- No stale state issues after switching back to wibatemp

### If test fails

- Restore wibatemp immediately (step 6)
- Check I2C bus: `i2cdetect -y 1` (should show device at 0x10)
- Check redhouse logs for error details
- Fix issue and re-test before proceeding with migration

---

## Migration Sequence

The migration happens in this order:

1. Set up /opt/redhouse-staging (takes over staging role)
2. Stop /opt/redhouse staging timers
3. Start /opt/redhouse-staging timers (staging continues uninterrupted)
4. Switch /opt/redhouse to production mode (all buckets + STAGING_MODE)
5. Pump control test
6. Temperature collection test (24h)
7. Gradual migration of remaining features

---

## Step 1: Staging Environment Setup

### 1a. Create staging directory

```bash
sudo mkdir -p /opt/redhouse-staging
sudo chown pi:pi /opt/redhouse-staging
cd /opt/redhouse-staging
git clone git@github.com:vvaten/redhouse.git .
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 1b. Create staging .env

Copy from /opt/redhouse (already has staging bucket names):

```bash
cp /opt/redhouse/.env /opt/redhouse-staging/.env
```

Verify:
```bash
grep STAGING_MODE /opt/redhouse-staging/.env   # Should be true
grep BUCKET /opt/redhouse-staging/.env          # Should all be *_staging
```

### 1c. Copy sensors.yaml

```bash
cp /opt/redhouse/config/sensors.yaml /opt/redhouse-staging/config/sensors.yaml
```

### 1d. Initial data copy

```bash
python deployment/copy_production_to_staging.py --days 30
```

### 1e. Generate staging systemd units

```bash
sudo deployment/generate_staging_systemd.sh
```

### 1f. Set up daily data copy

Add to Pi crontab:
```bash
0 4 * * * cd /opt/redhouse-staging && venv/bin/python deployment/copy_production_to_staging.py --days 2 >> /var/log/redhouse/staging-data-copy.log 2>&1
```

### 1g. Verify staging works

```bash
cd /opt/redhouse-staging
source venv/bin/activate
python collect_temperatures.py --dry-run --verbose
python generate_heating_program_v2.py --dry-run
```

---

## Step 2: Hand off staging to /opt/redhouse-staging

```bash
# Stop current staging timers on /opt/redhouse
sudo systemctl stop redhouse-*.timer

# Start staging timers on /opt/redhouse-staging
sudo deployment/staging_timers.sh start

# Verify staging data still flowing
sudo deployment/staging_timers.sh status
```

---

## Step 3: Switch /opt/redhouse to production mode

With all /opt/redhouse timers stopped and staging running on
/opt/redhouse-staging, switch /opt/redhouse to production mode.

```bash
sudo -u pi nano /opt/redhouse/.env
# Change: STAGING_MODE=false
# Change ALL bucket names to production:
#   INFLUXDB_BUCKET_TEMPERATURES=temperatures
#   INFLUXDB_BUCKET_WEATHER=weather
#   INFLUXDB_BUCKET_SPOTPRICE=spotprice
#   INFLUXDB_BUCKET_EMETERS=emeters
#   INFLUXDB_BUCKET_CHECKWATT=checkwatt_full_data
#   INFLUXDB_BUCKET_SHELLY_EM3_RAW=shelly_em3_emeters_raw
#   INFLUXDB_BUCKET_LOAD_CONTROL=load_control
#   INFLUXDB_BUCKET_EMETERS_5MIN=emeters_5min
#   INFLUXDB_BUCKET_ANALYTICS_15MIN=analytics_15min
#   INFLUXDB_BUCKET_ANALYTICS_1HOUR=analytics_1hour
#   INFLUXDB_BUCKET_WINDPOWER=windpower
```

Do NOT start any timers yet. Verify the config loads:

```bash
cd /opt/redhouse && source venv/bin/activate
python -c "from src.common.config import get_config; c = get_config(); print('Mode:', 'PRODUCTION'); print('Curve:', c.heating_curve); print('EVU:', c.evuoff_threshold_price)"
```

/opt/redhouse is now in production mode but idle.
Wibatemp cron is still running all production tasks.

---

## Step 4: Pump Control Test

(Same as before - see Pump Control Test section above)

---

## Step 5: Temperature Collection Test

Verify redhouse writes temperatures (and humidities) to production buckets
in the same format as wibatemp. Run for one full day.

/opt/redhouse is already in production mode (Step 3). No .env changes needed.

### Test procedure

```bash
# 1. Backup crontab
crontab -l > /home/pi/crontab_backup_$(date +%Y%m%d).txt

# 2. Stop wibatemp temperature collection
crontab -e
# Comment out line 28: run_wibatemp.sh

# 3. Start redhouse temperature timer
sudo systemctl start redhouse-temperature.timer

# 4. Verify first data point appears (wait 1-2 minutes)
journalctl -u redhouse-temperature.service --since "2 min ago"
# Check Grafana production dashboard: temperature data flowing
# Check: Shelly HT sensors included (Autotalli, PaaMH3, etc.)
# Check: Humidity data in humidities measurement

# 5. Monitor throughout the day
# Check for:
#   - All sensors reporting (including intermittent ones like Eteinen)
#   - Humidity data flowing
#   - No duplicate or missing data points
#   - Shelly HT sensors included
#   - Sensor names match wibatemp names in Grafana

# 6. After 24 hours, verify in Grafana
# - No gaps in temperature graphs
# - All sensor names match wibatemp names
# - Humidity data present
```

### If successful

Leave redhouse temperature running. First feature migrated.
Do NOT re-enable wibatemp temperature cron.
Proceed to gradual migration of remaining features.

### If problems found

```bash
# 1. Stop redhouse temperature timer
sudo systemctl stop redhouse-temperature.timer

# 2. Restore wibatemp
crontab /home/pi/crontab_backup_YYYYMMDD.txt

# 3. Fix issue in /opt/redhouse-staging, test, deploy to
#    /opt/redhouse, re-test
```

---

## Gradual Migration Steps

/opt/redhouse is in production mode with temperature timer running.
Each step: disable wibatemp cron line, start redhouse timer,
verify in Grafana. Keep crontab backup before each change.

### Per-step procedure

Each step: disable wibatemp cron line, enable redhouse systemd timer,
verify in Grafana after 24h. Keep crontab backup before each change.

### Step 0: Backup crontab

```bash
crontab -l > /home/pi/crontab_backup_$(date +%Y%m%d).txt
```

### Step 1: Read-only API collectors (lowest risk)

These only fetch data from external APIs and write to InfluxDB.
No hardware interaction. Safe to switch at any time.

| Wibatemp cron | Redhouse timer | Verification |
|---------------|----------------|--------------|
| Line 46: get_weather.py (hourly :02) | redhouse-weather.timer | weather bucket has data |
| Line 48: windpowergetter (hourly :04) | redhouse-windpower.timer | windpower bucket has data |
| Line 31-32: spot_price_getter (13:29-15:59) | redhouse-spot-prices.timer | spotprice bucket has data |
| Line 47: predict_solar_yield (hourly :03) | redhouse-solar-prediction.timer | emeters bucket has solar_yield |
| Line 39: checkwatt_dataloader (every 5min) | redhouse-checkwatt.timer | checkwatt_full_data bucket |

For each:
```bash
# 1. Comment out wibatemp cron line
crontab -e

# 2. Restart redhouse timer
sudo systemctl restart redhouse-<service>.timer

# 3. Verify after next scheduled run
journalctl -u redhouse-<service>.service --since "5 min ago"
```

### Step 2: Hardware collectors (medium risk)

These interact with hardware on the Pi. Only one writer per data source.

**2a. Temperature collection**

Pre-req: humidity gap must be fixed first.

```bash
# Disable wibatemp (line 28)
crontab -e  # comment out line 28

# Enable redhouse
sudo systemctl restart redhouse-temperature.timer

# Verify: temperatures AND humidities bucket still getting data
```

**2b. Shelly EM3 energy collection**

Replaces both fissio SBFspot script and shelly_ht_to_fissio.

```bash
# Disable wibatemp (lines 38, 43, 45)
crontab -e  # comment out lines 38, 43, 45

# Enable redhouse
sudo systemctl restart redhouse-shelly-em3.timer

# Verify: shelly_em3_emeters_raw bucket has data
```

### Step 3: Aggregation pipeline (new, no wibatemp equivalent)

Enable after temperature and shelly data are flowing from redhouse:

```bash
sudo systemctl restart redhouse-aggregate-emeters-5min.timer
sudo systemctl restart redhouse-aggregate-analytics-15min.timer
sudo systemctl restart redhouse-aggregate-analytics-1hour.timer
```

### Step 4: Heating control (critical -- atomic switch)

This is the most critical step. Only one system can control the pump.

**Preparation (do this 2-3 days before):**

1. Compare heating programs daily:
```bash
# Generate with redhouse (dry-run, does not affect production)
cd /opt/redhouse
source venv/bin/activate
python generate_heating_program_v2.py --dry-run --date-offset 0

# Compare against wibatemp's output
cat /home/pi/wibatemp/data/heating_program_*.json | python -m json.tool
```

2. Verify EVU-OFF periods look reasonable (threshold 0.40 EUR/kWh)
3. Verify heating hours match expected curve (12h at -20C, 6h at 0C, 2h at 16C)

**Cutover (do right after 16:05 when fresh program exists):**

```bash
# 1. Disable wibatemp heating control
crontab -e
# Comment out:
#   Line 33: generate_heating_program (16:05)
#   Line 34: execute_heating_program (*/15)
#   Line 35: mlp_cycle_evu_off (*/2 :23)
#   Line 27: @reboot mlp_control.sh restore

# 2. Enable redhouse heating control
sudo systemctl restart redhouse-generate-program.timer
sudo systemctl restart redhouse-execute-program.timer

# 3. Monitor closely
journalctl -u redhouse-execute-program.service -f
journalctl -u redhouse-generate-program.service -f
```

**Verify within first hour:**
- Pump command executed at next :00/:15/:30/:45
- No errors in journal
- Grafana load_control bucket shows data

**Fallback (if problems):**
```bash
# Stop redhouse heating control
sudo systemctl stop redhouse-execute-program.timer
sudo systemctl stop redhouse-generate-program.timer

# Restore wibatemp
crontab /home/pi/crontab_backup_YYYYMMDD.txt

# Restore pump state
sudo /home/pi/wibatemp/mlp_control.sh restore
```

### Step 5: Health monitoring and backup

After all features migrated:

```bash
sudo systemctl restart redhouse-health-check.timer
sudo systemctl restart redhouse-backup.timer
```

### Step 6: Cleanup

After 1 week of stable production:

- [ ] Remove all wibatemp cron entries (keep only pinglogger + i2c reboot)
- [ ] Remove old .env values that moved to config.yaml (cosmetic)
- [ ] Verify production Grafana dashboard
- [ ] Document any operational differences from wibatemp

---

## Crontab Lines to Keep

These wibatemp cron entries are NOT replaced by redhouse and must stay:

| Line | Entry | Reason |
|------|-------|--------|
| 29 | pinglogger | Not ported yet (TODO in PLAN.md) |
| 44 | @reboot set_i2c_wire_configuration.sh | Hardware setup, still needed |

---

## Data Compatibility Verification

Both systems write identical formats to InfluxDB:

| Bucket | Measurement | Wibatemp | Redhouse | Compatible? |
|--------|-------------|----------|----------|-------------|
| temperatures | temperatures | suffix-mapped names | same suffix mapping | YES |
| temperatures | humidities | writes humidity | NOT YET -- needs fix | FIX NEEDED |
| weather | weather | FMI field names | identical | YES |
| spotprice | spot | field names | identical | YES |
| checkwatt_full_data | checkwatt | 6 fields | identical 6 fields | YES |
| emeters | various | Shelly/SBFspot | Shelly EM3 | YES |
| load_control | load_control | N/A (JSON files) | new feature | N/A |

---

## Rollback Plan

At any point, full rollback to wibatemp:

```bash
# Stop all redhouse services
sudo systemctl stop redhouse-*.timer

# Restore full wibatemp crontab
crontab /home/pi/crontab_backup_YYYYMMDD.txt

# Restore pump state
sudo /home/pi/wibatemp/mlp_control.sh restore
```

---

## Timeline (estimated)

| Week | Phase | Steps |
|------|-------|-------|
| 0 | Pre-requisites | Fix humidity, deploy scripts, prod dashboard |
| 1 | Read-only collectors | Steps 1 (weather, wind, spot, solar, checkwatt) |
| 2 | Hardware collectors | Steps 2-3 (temperature, shelly, aggregation) |
| 3 | Heating control | Step 4 (program gen + exec, pump control) |
| 4 | Stabilize | Step 5-6 (health, backup, cleanup) |
