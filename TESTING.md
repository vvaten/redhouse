# Testing Guide

This guide explains how to safely test the refactored code without disrupting production data collection.

## Prerequisites

### 1. Create Test Buckets in InfluxDB

Before testing, you need to create test buckets in InfluxDB. You can do this via the InfluxDB UI or CLI:

**Option A: Via InfluxDB Web UI**
1. Go to http://192.168.1.164:8086
2. Navigate to "Load Data" > "Buckets"
3. Create the following buckets:
   - `temperatures_test` (retention: same as production, e.g., 30d)
   - `weather_test` (retention: same as production)
   - `spotprice_test` (retention: same as production)
   - `emeters_test` (retention: same as production)
   - `checkwatt_full_data_test` (retention: same as production)

**Option B: Via InfluxDB CLI**
```bash
# On your NAS or wherever InfluxDB is running
influx bucket create -n temperatures_test -o area51 -r 30d
influx bucket create -n weather_test -o area51 -r 30d
influx bucket create -n spotprice_test -o area51 -r 30d
influx bucket create -n emeters_test -o area51 -r 30d
influx bucket create -n checkwatt_full_data_test -o area51 -r 30d
```

### 2. Set Up Test Environment

```bash
# On your development machine or Raspberry Pi
cd /c/Projects/redhouse  # or ~/redhouse on Pi

# Create virtual environment (if not already created)
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate  # On Linux/Mac
# or
./venv/Scripts/activate   # On Windows

# Install dependencies
pip install -r requirements.txt

# Copy test environment configuration
cp .env.test .env

# Edit .env with your actual InfluxDB token
nano .env  # or vim, or any editor
```

## Testing Methods

### Method 1: Dry-Run Mode (Safest - No Database Writes)

Test temperature collection without writing anything to InfluxDB:

```bash
# Activate venv first
source venv/bin/activate

# Dry-run mode (only logs what would be written)
python collect_temperatures.py --dry-run

# Verbose dry-run mode (more detailed logging)
python collect_temperatures.py --dry-run --verbose
```

**What it does:**
- Reads actual sensor values from `/sys/bus/w1/devices/`
- Processes and validates the data
- Logs what would be written to InfluxDB
- Does NOT write anything to the database

### Method 2: Write to Test Buckets

Test the full pipeline by writing to test buckets:

```bash
# Make sure .env is configured with test buckets
# (should be already if you copied .env.test)

# Run temperature collection
python collect_temperatures.py

# Check the logs
tail -f logs_test/temperature.log
```

**What it does:**
- Reads sensor values
- Writes to `temperatures_test` bucket in InfluxDB
- Production data remains untouched

**Verify in Grafana:**
1. Create a temporary dashboard
2. Query the `temperatures_test` bucket
3. Verify data looks correct

### Method 3: Side-by-Side Testing

Run both old and new code simultaneously for comparison:

```bash
# Old code keeps running via crontab
# Manually run new code every minute for comparison

# Create a test script
cat > test_side_by_side.sh << 'EOF'
#!/bin/bash
cd /opt/home-automation
source venv/bin/activate
python collect_temperatures.py
EOF

chmod +x test_side_by_side.sh

# Run it manually or via a temporary cron
# Compare data quality in Grafana between buckets
```

## Unit Tests (Always Safe)

Run unit tests at any time - they mock hardware and never touch InfluxDB:

```bash
# Activate venv
source venv/bin/activate

# Run all tests
pytest tests/

# Run only temperature tests
pytest tests/unit/test_temperature.py -v

# Run with coverage report
pytest tests/ --cov=src --cov-report=html
```

## Deployment to Production

Once you're confident the code works:

### Step 1: Create Backup
```bash
# On Raspberry Pi
crontab -l > ~/crontab_backup_$(date +%Y%m%d).txt
```

### Step 2: Switch to Production Config
```bash
# Update .env to use production buckets
cp .env.example .env
nano .env  # Add your actual credentials
```

### Step 3: Gradual Rollout

**Option A: Manual switch (recommended)**
```bash
# Stop old cron job (comment it out)
crontab -e
# Comment out: * * * * * sudo  /home/pi/wibatemp/run_wibatemp.sh

# Test new script manually a few times
cd /opt/home-automation
source venv/bin/activate
python collect_temperatures.py

# If working, add to crontab
* * * * * cd /opt/home-automation && venv/bin/python collect_temperatures.py
```

**Option B: Parallel run (more cautious)**
```bash
# Keep old cron running
# Add new cron with production config
* * * * * cd /opt/home-automation && venv/bin/python collect_temperatures.py

# Monitor for a day or two
# Compare data quality
# Remove old cron when confident
```

## Troubleshooting

### No sensor data collected
```bash
# Check if 1-wire is enabled
ls /sys/bus/w1/devices/

# Check sensor files
cat /sys/bus/w1/devices/28-*/w1_slave

# Run with verbose logging
python collect_temperatures.py --dry-run --verbose
```

### InfluxDB connection errors
```bash
# Test connection
curl -I http://192.168.1.164:8086/health

# Verify token works
influx bucket list --org area51 --token YOUR_TOKEN
```

### Import errors
```bash
# Make sure venv is activated
which python  # Should show venv path

# Reinstall dependencies
pip install -r requirements.txt
```

## Cleanup After Testing

Once deployed to production, you can delete test buckets to save space:

```bash
# Via InfluxDB UI or CLI
influx bucket delete -n temperatures_test -o area51
influx bucket delete -n weather_test -o area51
# etc.
```

## Need Help?

- Check logs in `logs_test/` directory
- Run with `--dry-run --verbose` for detailed debugging
- Review unit tests for expected behavior
- Check [README.md](README.md) for general setup info
