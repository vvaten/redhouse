# Staging Deployment Guide

Complete step-by-step guide for deploying RedHouse in staging mode alongside your existing production system.

## Overview

Staging mode allows you to run the new RedHouse system in parallel with your old system:
- New system collects data and generates programs using `*_staging` buckets
- New system logs what it *would* do but doesn't control hardware
- Old system continues controlling hardware normally
- You can compare production vs staging data side-by-side in Grafana

---

## Prerequisites

- [ ] Raspberry Pi with SSH access
- [ ] InfluxDB server accessible at 192.168.1.164:8086
- [ ] Grafana accessible at 192.168.1.164:3000
- [ ] InfluxDB credentials (token, org)
- [ ] CheckWatt API credentials

---

## Step 1: Create InfluxDB Staging Buckets

### Option A: InfluxDB Web UI (Easiest)

1. Open http://192.168.1.164:8086
2. Login with your InfluxDB credentials
3. Go to **Data** → **Buckets**
4. Click **Create Bucket** (blue button, top right)
5. Create these 6 buckets (org: `area51`, retention: infinite or default):
   - `temperatures_staging`
   - `weather_staging`
   - `spotprice_staging`
   - `emeters_staging`
   - `checkwatt_staging`
   - `load_control_staging`

### Option B: InfluxDB CLI (Faster if available)

```bash
# SSH to InfluxDB server or configure influx CLI locally
influx config create \
  --config-name redhouse \
  --host-url http://192.168.1.164:8086 \
  --org area51 \
  --token YOUR_INFLUXDB_TOKEN \
  --active

# Create buckets
influx bucket create -n temperatures_staging
influx bucket create -n weather_staging
influx bucket create -n spotprice_staging
influx bucket create -n emeters_staging
influx bucket create -n checkwatt_staging
influx bucket create -n load_control_staging
```

### Option C: Use Helper Script

```bash
# After deploying to Raspberry Pi
cd /opt/redhouse
chmod +x deployment/create_staging_buckets.sh
./deployment/create_staging_buckets.sh
```

**Verify:** Check in InfluxDB UI that all 6 `*_staging` buckets exist.

---

## Step 2: Deploy RedHouse to Raspberry Pi

### First-Time Installation

```bash
# 1. SSH to Raspberry Pi
ssh pi@<raspberry-pi-ip>

# 2. Copy deployment script
scp deployment/deploy.sh pi@<raspberry-pi-ip>:/tmp/

# 3. Run deployment script as root
sudo /tmp/deploy.sh
```

### Update Existing Installation

```bash
# SSH to Raspberry Pi and run deployment script
ssh pi@<raspberry-pi-ip>
sudo /opt/redhouse/deployment/deploy.sh
```

**What the deployment script does:**
1. Clones/updates repository from GitHub
2. Creates/updates Python virtual environment
3. Installs dependencies
4. Runs unit tests (deployment aborts if tests fail)
5. Installs systemd services and timers
6. Restarts all timers

**Verify:** Unit tests should pass (138 tests)

---

## Step 3: Configure Environment Variables

### Create .env File

```bash
# SSH to Raspberry Pi
ssh pi@<raspberry-pi-ip>

# Create .env file
sudo nano /opt/redhouse/.env
```

### Required Configuration

Copy from `.env.example` and configure:

```bash
# InfluxDB Configuration
INFLUXDB_URL=http://192.168.1.164:8086
INFLUXDB_TOKEN=your-influxdb-token-here
INFLUXDB_ORG=area51

# IMPORTANT: Use staging buckets (temperatures can use production for read-only access)
INFLUXDB_BUCKET_TEMPERATURES=temperatures  # Read-only, avoids sensor hardware contention
INFLUXDB_BUCKET_WEATHER=weather_staging
INFLUXDB_BUCKET_SPOTPRICE=spotprice_staging
INFLUXDB_BUCKET_EMETERS=emeters_staging
INFLUXDB_BUCKET_CHECKWATT=checkwatt_staging
INFLUXDB_BUCKET_LOAD_CONTROL=load_control_staging

# Weather API (FMI)
WEATHER_LATLON=60.1699,24.9384

# CheckWatt API
CHECKWATT_USERNAME=your-email@example.com
CHECKWATT_PASSWORD=your-password-here
CHECKWATT_METER_IDS=191624,213110,213111,213112,213113,213114

# Hardware Configuration
PUMP_I2C_BUS=1
PUMP_I2C_ADDRESS=0x10
SHELLY_RELAY_URL=http://192.168.1.5

# CRITICAL: Enable staging mode (no hardware control, blocks production writes)
STAGING_MODE=true

# Heating Configuration
HEATING_CURVE_MINUS20=12
HEATING_CURVE_0=8
HEATING_CURVE_16=4
EVUOFF_THRESHOLD_PRICE=0.20
EVUOFF_MAX_CONTINUOUS_HOURS=4

# Spot Price Configuration
SPOT_VALUE_ADDED_TAX=1.255
SPOT_SELLERS_MARGIN=0.50
SPOT_PRODUCTION_BUYBACK_MARGIN=0.30
SPOT_TRANSFER_DAY_PRICE=2.59
SPOT_TRANSFER_NIGHT_PRICE=1.35
SPOT_TRANSFER_TAX_PRICE=2.79372

# Logging
LOG_LEVEL=INFO
LOG_DIR=/var/log/redhouse
LOG_MAX_BYTES=10485760
LOG_BACKUP_COUNT=5

# Deployment
DEPLOY_DIR=/opt/redhouse
```

**Save and exit:** Ctrl+O, Enter, Ctrl+X

**Verify:** Check file exists and has correct permissions
```bash
ls -la /opt/redhouse/.env
# Should be owned by pi:pi
```

---

## Step 4: Restart Services

```bash
# Restart all RedHouse timers to apply configuration
sudo systemctl restart redhouse-*.timer

# Verify services are running
systemctl list-timers redhouse-*
```

**Expected output:** All 7 timers should show as active with next run times

---

## Step 5: Monitor and Verify

### Watch Logs

```bash
# Watch program execution (most important)
journalctl -u redhouse-execute-program.service -f

# Watch program generation
journalctl -u redhouse-generate-program.service -f

# Watch all services
journalctl -u "redhouse-*" --since "5 minutes ago"
```

### Look For Staging Mode Indicators

You should see log messages like:
- `"PumpController initialized in STAGING mode (no hardware control)"`
- `"Initialized HeatingProgramExecutor v2.0.0 (STAGING mode)"`
- `"DRY-RUN: Would execute pump command: ON"`
- `"STAGING: Would execute command X"`

### Check Service Status

```bash
# List all timers and next run times
systemctl list-timers redhouse-*

# Check specific service status
systemctl status redhouse-execute-program.service
systemctl status redhouse-generate-program.service

# Check for failed services
systemctl --failed | grep redhouse
```

### Verify Data Collection

After 5-10 minutes, check InfluxDB:
1. Open http://192.168.1.164:8086
2. Go to **Data Explorer**
3. Select `temperatures_staging` bucket
4. Verify data is being collected

---

## Step 6: Populate Staging with Historical Data (Optional)

To test program generation with real data, copy production data to staging:

### Option A: Run on Raspberry Pi (Slower)

```bash
cd /opt/redhouse

# Dry-run first (see what would be copied)
venv/bin/python -u deployment/copy_production_to_staging.py --days 30 --dry-run

# Copy last 30 days of data
venv/bin/python -u deployment/copy_production_to_staging.py --days 30

# Or copy specific date range
venv/bin/python -u deployment/copy_production_to_staging.py --start 2024-10-01 --end 2024-10-31

# Or copy only specific buckets
venv/bin/python -u deployment/copy_production_to_staging.py --days 7 --buckets temperatures weather spotprice
```

**Note:** This copies data over the network and may take 15-30 minutes for 30 days of data.

### Option B: Run on InfluxDB Server (Faster - Recommended)

Running directly on the InfluxDB server is much faster (no network overhead):

```bash
# SSH to InfluxDB server
ssh user@192.168.1.164

# Clone repository (temporary - just for this script)
cd /tmp
git clone https://github.com/vvaten/redhouse.git
cd redhouse

# Install minimal dependencies
python3 -m venv venv
venv/bin/pip install influxdb-client python-dotenv

# Create minimal .env with InfluxDB credentials only
cat > .env << 'EOF'
INFLUXDB_URL=http://localhost:8086
INFLUXDB_TOKEN=your-token-here
INFLUXDB_ORG=area51
EOF

# Run copy script
venv/bin/python -u deployment/copy_production_to_staging.py --days 30

# Cleanup when done
cd /tmp
rm -rf redhouse
```

**Advantage:** 5-10x faster due to local disk access and no network latency.

---

## Step 7: Create Staging Grafana Dashboard

### Create Grafana API Key

1. Open http://192.168.1.164:3000
2. Go to **Configuration** → **API Keys**
3. Click **Add API key**
   - Name: `dashboard-cloner`
   - Role: **Editor**
4. Copy the generated key

### Add API Key to .env

```bash
sudo nano /opt/redhouse/.env

# Add these lines:
GRAFANA_URL=http://192.168.1.164:3000
GRAFANA_API_KEY=your-copied-api-key-here
```

### Find Dashboard UID

1. Open your production dashboard in Grafana
2. Look at the URL: `http://192.168.1.164:3000/d/ABC123/dashboard-name`
3. The UID is `ABC123` (the part after `/d/`)

### Clone Dashboard

```bash
cd /opt/redhouse

# Dry-run first (see what would change)
venv/bin/python -u deployment/clone_grafana_dashboard_to_staging.py \
  --dashboard-uid ABC123 --dry-run

# Actually create the staging dashboard
venv/bin/python -u deployment/clone_grafana_dashboard_to_staging.py \
  --dashboard-uid ABC123
```

**Result:** Script will output URL to your new staging dashboard

### View Side-by-Side

Open both dashboards in separate browser tabs:
- Production: `http://192.168.1.164:3000/d/ABC123/dashboard-name`
- Staging: `http://192.168.1.164:3000/d/XYZ789/dashboard-name-staging`

Compare data collection and program generation between old and new systems!

---

## Step 8: Validation Period

Run in staging mode for at least **1-2 weeks** to validate:

### Daily Checks

- [ ] Data collection running (all 7 timers executing)
- [ ] No errors in logs: `journalctl -u "redhouse-*" -p err --since today`
- [ ] Staging buckets receiving data
- [ ] Program generation succeeding daily
- [ ] Program execution logging what it would do

### Weekly Checks

- [ ] Compare staging vs production data in Grafana
- [ ] Verify heating programs look reasonable
- [ ] Check that staging system generates similar programs to production
- [ ] Monitor disk space: `df -h`
- [ ] Check systemd journal size: `journalctl --disk-usage`

### Known Differences to Expect

- Staging won't control hardware (by design)
- Staging may generate slightly different programs (new optimization logic)
- Data collection timing may differ slightly from old system

---

## Step 9: Switch to Production

**ONLY when confident staging is working correctly!**

### 1. Stop Old System

```bash
# SSH to Raspberry Pi
ssh pi@<raspberry-pi-ip>

# Disable old cron jobs
crontab -e
# Comment out or remove all old redhouse cron entries

# Stop any old scripts/services
# (Check what's currently running with: ps aux | grep python)
```

### 2. Update .env for Production

```bash
sudo nano /opt/redhouse/.env
```

**Change these lines:**
```bash
# Use production buckets (remove _staging suffix)
INFLUXDB_BUCKET_TEMPERATURES=temperatures
INFLUXDB_BUCKET_WEATHER=weather
INFLUXDB_BUCKET_SPOTPRICE=spotprice
INFLUXDB_BUCKET_EMETERS=emeters
INFLUXDB_BUCKET_CHECKWATT=checkwatt_full_data
INFLUXDB_BUCKET_LOAD_CONTROL=load_control

# CRITICAL: Disable staging mode (enable hardware control!)
STAGING_MODE=false
```

### 3. Restart Services

```bash
# Restart to apply production configuration
sudo systemctl restart redhouse-*.timer

# Verify production mode in logs
journalctl -u redhouse-execute-program.service -n 50 | grep -i "production\|staging"
```

**Expected:** Should see "PRODUCTION mode" (not "STAGING mode")

### 4. Monitor Closely for 24 Hours

```bash
# Watch program execution continuously
journalctl -u redhouse-execute-program.service -f

# Watch for errors
journalctl -u "redhouse-*" -p err -f
```

**Verify:**
- Hardware commands are actually executing (not "DRY-RUN")
- Pump is being controlled correctly
- No errors in logs
- Heating is working as expected

### 5. First 24-Hour Checklist

- [ ] Pump responded to ON/ALE/EVU commands
- [ ] EVU cycling happening automatically (every 105 min)
- [ ] Shelly relay being controlled
- [ ] House temperature maintained correctly
- [ ] No errors in logs
- [ ] Program generation still working
- [ ] Data collection continuing normally

---

## Troubleshooting

### Services Not Starting

```bash
# Check service status
systemctl status redhouse-execute-program.service

# View detailed logs
journalctl -xe -u redhouse-execute-program.service

# Common issues:
# - .env file missing or wrong permissions
# - Python dependencies not installed
# - InfluxDB credentials incorrect
```

### No Data in Staging Buckets

```bash
# Check service logs
journalctl -u redhouse-temperature.service -n 50

# Verify .env has correct bucket names
grep INFLUXDB_BUCKET /opt/redhouse/.env

# Test InfluxDB connection
cd /opt/redhouse
venv/bin/python -u -c "from src.common.influx_client import InfluxClient; from src.common.config import get_config; c = InfluxClient(get_config()); print('Connection OK')"
```

### Hardware Still Being Controlled in Staging

**This is a critical error!**

```bash
# Immediately verify STAGING_MODE is set
grep STAGING_MODE /opt/redhouse/.env
# Should show: STAGING_MODE=true

# Check logs for mode
journalctl -u redhouse-execute-program.service | grep -i "staging\|dry-run"

# If hardware is still being controlled, stop services immediately:
sudo systemctl stop redhouse-*.timer
```

### Program Generation Failing

```bash
# View generation logs
journalctl -u redhouse-generate-program.service -n 100

# Common issues:
# - Not enough historical data (need at least 1 day)
# - InfluxDB query errors
# - Configuration errors (heating curve, spot prices)

# Test manually
cd /opt/redhouse
venv/bin/python -u generate_heating_program_v2.py --verbose --dry-run
```

### Grafana Dashboard Clone Failed

```bash
# Check API key is valid
curl -H "Authorization: Bearer YOUR_API_KEY" \
  http://192.168.1.164:3000/api/dashboards/uid/ABC123

# Common issues:
# - Invalid API key
# - Wrong dashboard UID
# - API key doesn't have Editor role
```

---

## Rollback Procedure

If you need to rollback from production to staging:

```bash
# 1. Stop services
sudo systemctl stop redhouse-*.timer

# 2. Edit .env back to staging configuration
sudo nano /opt/redhouse/.env
# Set STAGING_MODE=true
# Set buckets back to *_staging

# 3. Restart services
sudo systemctl restart redhouse-*.timer

# 4. Restart old system
crontab -e  # Re-enable old cron jobs
```

---

## Useful Commands Reference

```bash
# View all RedHouse timers
systemctl list-timers redhouse-*

# Check if staging mode is enabled
grep STAGING_MODE /opt/redhouse/.env

# Watch real-time logs
journalctl -u redhouse-execute-program.service -f

# View logs from last hour
journalctl -u "redhouse-*" --since "1 hour ago"

# View only errors
journalctl -u "redhouse-*" -p err --since today

# Check disk usage
df -h /opt/redhouse
journalctl --disk-usage

# Manually trigger a service
sudo systemctl start redhouse-generate-program.service

# Restart all services
sudo systemctl restart redhouse-*.timer

# Stop all services
sudo systemctl stop redhouse-*.timer

# View service configuration
systemctl cat redhouse-execute-program.service
```

---

## Bucket Naming Convention

| Environment | Bucket Pattern | Example | Purpose |
|-------------|---------------|---------|---------|
| Production | `name` | `temperatures` | Live data, hardware control enabled |
| Staging | `name_staging` | `temperatures_staging` | Testing, hardware control disabled |
| Tests | `name_test` | `temperatures_test` | Unit/integration tests |

---

## Support

- **Documentation**: `/opt/redhouse/deployment/README.md`
- **Logs**: `journalctl -u "redhouse-*" -f`
- **GitHub**: https://github.com/vvaten/redhouse
- **Modernization Plan**: `/opt/redhouse/MODERNIZATION_PLAN.md`

---

## Summary Checklist

- [ ] Created 6 staging buckets in InfluxDB
- [ ] Deployed RedHouse to Raspberry Pi
- [ ] Created `.env` file with staging buckets and `STAGING_MODE=true`
- [ ] Restarted services
- [ ] Verified logs show "STAGING mode"
- [ ] Verified no hardware control happening
- [ ] Data collection running in staging buckets
- [ ] (Optional) Copied historical data to staging
- [ ] (Optional) Created staging Grafana dashboard
- [ ] Monitored for 1-2 weeks in staging
- [ ] Switched to production (changed .env, restarted services)
- [ ] Monitored closely for 24 hours in production
- [ ] Old system disabled
- [ ] Everything working correctly!
