# Quick Reference Guide

Essential commands and workflows for the redhouse home automation project.

---

## Before You Start

### Check Your Configuration

```bash
# What environment am I using?
cat .env | grep BUCKET

# Test environment (safe):
INFLUXDB_BUCKET_TEMPERATURES=temperatures_test  ✅

# Production environment (careful!):
INFLUXDB_BUCKET_TEMPERATURES=temperatures  ⚠️

# Switch to test environment:
cp .env.test .env
# Then edit .env with your actual token
```

---

## Testing Workflows

### 1. Unit Tests (Always Safe)

```bash
# Activate venv
source venv/bin/activate  # Linux/Mac
./venv/Scripts/activate   # Windows

# Run all tests
pytest tests/unit/ -v

# Run specific module tests
pytest tests/unit/test_temperature.py -v

# With coverage
pytest tests/unit/ --cov=src --cov-report=html
```

### 2. Dry-Run Testing (No DB Writes)

```bash
# Temperature collection (reads sensors, doesn't write)
python collect_temperatures.py --dry-run --verbose

# See exactly what would be written
# No changes to database
```

### 3. Integration Tests (Writes to Test Buckets)

```bash
# Verify test buckets exist
python scripts/setup_test_buckets.py

# Test InfluxDB connection and write
python tests/integration/test_influx_connection.py

# Run actual collection (writes to test bucket)
python collect_temperatures.py
```

### 4. Data Verification

```bash
# Check if test data exists
python scripts/find_test_data.py

# View in Grafana
# 1. Create temporary dashboard
# 2. Query temperatures_test bucket
# 3. Verify data looks correct
```

---

## Common Tasks

### Temperature Collection

```bash
# Dry-run (no writes)
python collect_temperatures.py --dry-run

# Write to test bucket
python collect_temperatures.py

# Verbose logging
python collect_temperatures.py --verbose

# Combined
python collect_temperatures.py --dry-run --verbose
```

### Managing Test Data

```bash
# Find test sensor data
python scripts/find_test_data.py

# Remove test data at specific timestamp (dry-run first!)
python scripts/fix_test_data.py "2025-10-18T18:30:53.770991Z" --dry-run
python scripts/fix_test_data.py "2025-10-18T18:30:53.770991Z" --confirm
```

### InfluxDB Management

```bash
# List buckets
python scripts/setup_test_buckets.py

# Test connection
python tests/integration/test_influx_connection.py
```

---

## Git Workflows

### Daily Development

```bash
# Check status
git status

# Stage changes
git add src/data_collection/temperature.py tests/unit/test_temperature.py

# Commit with message
git commit -m "Add feature X

- Detail 1
- Detail 2

Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"

# Push to GitHub
git push
```

### View History

```bash
# Recent commits
git log --oneline -10

# Changes in specific commit
git show <commit-hash>

# File history
git log --follow -- src/data_collection/temperature.py
```

---

## Python Virtual Environment

### Setup (First Time)

```bash
# Create venv
python3 -m venv venv

# Activate
source venv/bin/activate  # Linux/Mac
./venv/Scripts/activate   # Windows

# Install dependencies
pip install -r requirements.txt
```

### Daily Use

```bash
# Activate
source venv/bin/activate  # Linux/Mac
./venv/Scripts/activate   # Windows

# Verify active
which python  # Should show venv path

# Deactivate when done
deactivate
```

---

## Environment Configuration

### Switch Between Test and Production

```bash
# TESTING (use test buckets)
cp .env.test .env
nano .env  # Add your actual InfluxDB token

# PRODUCTION (use production buckets)
cp .env.example .env
nano .env  # Add all credentials

# Verify
cat .env | grep BUCKET
```

---

## Deployment (Future Reference)

### On Raspberry Pi

```bash
# Initial setup
cd /opt/home-automation
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Copy production config
cp .env.example .env
nano .env  # Add actual credentials

# Test manually
python collect_temperatures.py --dry-run
python collect_temperatures.py

# Add to crontab
crontab -e
# Add: * * * * * cd /opt/home-automation && venv/bin/python collect_temperatures.py
```

---

## Troubleshooting

### Import Errors

```bash
# Make sure venv is activated
which python  # Check path

# Reinstall dependencies
pip install -r requirements.txt

# Check sys.path
python -c "import sys; print('\n'.join(sys.path))"
```

### InfluxDB Connection Errors

```bash
# Test connection
curl -I http://192.168.1.164:8086/health

# Test with Python
python tests/integration/test_influx_connection.py

# Check credentials in .env
cat .env | grep INFLUX
```

### Sensor Reading Errors

```bash
# Check 1-wire devices
ls /sys/bus/w1/devices/

# Read raw sensor
cat /sys/bus/w1/devices/28-*/w1_slave

# Run with verbose logging
python collect_temperatures.py --dry-run --verbose
```

---

## File Locations

### Source Code
- `src/data_collection/temperature.py` - Temperature collection module
- `src/common/config.py` - Configuration loader
- `src/common/logger.py` - Logging setup
- `src/common/influx_client.py` - InfluxDB client wrapper

### Tests
- `tests/unit/test_temperature.py` - Unit tests
- `tests/integration/test_influx_connection.py` - Integration tests

### Scripts
- `collect_temperatures.py` - Main temperature collection script
- `scripts/find_test_data.py` - Find test data in InfluxDB
- `scripts/fix_test_data.py` - Remove test data from specific timestamp
- `scripts/setup_test_buckets.py` - Create test buckets

### Configuration
- `.env` - Active configuration (not in git)
- `.env.example` - Production template
- `.env.test` - Test template
- `config/config.yaml.example` - System configuration

### Documentation
- `README.md` - Project overview and setup
- `TESTING.md` - Testing guide
- `MODERNIZATION_PLAN.md` - Project roadmap
- `docs/LESSONS_LEARNED.md` - Lessons and best practices
- `docs/QUICK_REFERENCE.md` - This file

---

## Useful Commands

### Check Python Environment

```bash
# Python version
python --version

# Installed packages
pip list

# Show package details
pip show influxdb-client
```

### Format Code

```bash
# Format with black
black src/ tests/

# Lint with ruff
ruff check src/ tests/

# Type check (if mypy added)
mypy src/
```

### Check Disk Space

```bash
# On Raspberry Pi
df -h

# Check logs directory
du -sh /var/log/home-automation/

# Check InfluxDB data (on NAS)
# Via InfluxDB UI: Settings > Usage
```

---

## Emergency Procedures

### "I wrote bad data to production!"

```bash
# 1. Find the bad data
python scripts/find_test_data.py

# 2. Check what would be removed (dry-run)
python scripts/fix_test_data.py "TIMESTAMP" --dry-run

# 3. Remove it
python scripts/fix_test_data.py "TIMESTAMP" --confirm

# 4. Verify it's gone
python scripts/find_test_data.py
```

### "System is not collecting data!"

```bash
# Check if process is running
ps aux | grep collect_temperatures

# Check recent logs
tail -f logs/temperature.log

# Run manually to see errors
python collect_temperatures.py --verbose

# Check crontab
crontab -l
```

### "Rollback to previous version"

```bash
# See recent commits
git log --oneline -10

# Revert to specific commit
git checkout <commit-hash>

# Or create new commit that undoes changes
git revert <commit-hash>

# Push to GitHub
git push
```

---

## Getting Help

1. **Check documentation**
   - [README.md](../README.md) for setup
   - [TESTING.md](../TESTING.md) for testing
   - [LESSONS_LEARNED.md](LESSONS_LEARNED.md) for best practices

2. **Check logs**
   - `logs/temperature.log`
   - `logs_test/temperature.log` (when using test config)

3. **Run with verbose mode**
   ```bash
   python collect_temperatures.py --dry-run --verbose
   ```

4. **Check GitHub Issues**
   - Project issues and discussions

---

**Last Updated**: 2025-10-18
