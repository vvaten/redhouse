# RedHouse Deployment

This directory contains deployment automation for the RedHouse home automation system.

## Systemd Services

The system uses systemd services and timers to replace the old crontab-based scheduling. This provides better logging, service management, and reliability.

### Available Services

| Service | Schedule | Description |
|---------|----------|-------------|
| `redhouse-temperature` | Every 1 minute | Collect temperature sensor data |
| `redhouse-weather` | Hourly at :02 | Fetch weather forecasts from FMI |
| `redhouse-spot-prices` | 13:29, 13:59, 14:29, 14:59, 15:29, 15:59 | Fetch electricity spot prices |
| `redhouse-checkwatt` | Every 5 min (:01, :06, :11...) | Collect battery/solar data from CheckWatt |
| `redhouse-solar-prediction` | Hourly at :03 | Predict solar yield for optimization |
| `redhouse-generate-program` | Daily at 16:05 | Generate tomorrow's heating program |
| `redhouse-execute-program` | Every 15 min (:00, :15, :30, :45) | Execute heating program commands (includes smart EVU cycling) |

## Deployment

### Staging Mode (Recommended First Step)

Before switching from the old system to the new one, deploy in **staging mode** to run everything in parallel without hardware control.

#### Step 1: Create Staging Buckets in InfluxDB

```bash
# SSH to InfluxDB server or use InfluxDB UI
influx bucket create -n temperatures_staging -o area51 -r 0
influx bucket create -n weather_staging -o area51 -r 0
influx bucket create -n spotprice_staging -o area51 -r 0
influx bucket create -n emeters_staging -o area51 -r 0
influx bucket create -n checkwatt_staging -o area51 -r 0
influx bucket create -n load_control_staging -o area51 -r 0
```

**Bucket naming convention:**
- Production: `temperatures`, `weather`, etc. (used by old system)
- Staging: `temperatures_staging`, `weather_staging`, etc. (used by new system in staging)
- Tests: `temperatures_test`, `weather_test`, etc. (used by unit/integration tests)

#### Step 2: Deploy and Configure Staging Mode

```bash
# 1. Deploy the system normally
ssh pi@<raspberry-pi-ip>
sudo /opt/redhouse/deployment/deploy.sh

# 2. Configure staging mode in .env
sudo nano /opt/redhouse/.env
```

Add or update these lines:
```bash
# Use staging buckets (temperatures can use production for read-only access)
INFLUXDB_BUCKET_TEMPERATURES=temperatures  # Read-only, avoids sensor hardware contention
INFLUXDB_BUCKET_WEATHER=weather_staging
INFLUXDB_BUCKET_SPOTPRICE=spotprice_staging
INFLUXDB_BUCKET_EMETERS=emeters_staging
INFLUXDB_BUCKET_CHECKWATT=checkwatt_staging
INFLUXDB_BUCKET_LOAD_CONTROL=load_control_staging

# Enable staging mode (no hardware control, blocks production writes)
STAGING_MODE=true
```

```bash
# 3. Restart services to apply configuration
sudo systemctl restart redhouse-*.timer
```

#### Step 3: Monitor and Validate

In staging mode:
- All data collection runs normally → writes to `*_staging` buckets
- Temperature collection uses production bucket in READ-ONLY mode (writes blocked by validator)
- Program generation runs on schedule → reads from staging buckets (and production temperatures)
- Program execution runs → but NO hardware commands (I2C, Shelly relay)
- All actions logged with "STAGING" prefix
- Old system continues controlling hardware using production buckets

Monitor logs to verify everything works:
```bash
# Watch program execution
journalctl -u redhouse-execute-program.service -f

# Check all services
systemctl list-timers redhouse-*

# View recent program generation
journalctl -u redhouse-generate-program.service -n 100
```

Use Grafana to compare staging data vs production data side-by-side.

#### Step 4: Switch to Production

When confident the new system works correctly:

```bash
# 1. Update .env to use production buckets
sudo nano /opt/redhouse/.env
```

Change buckets back to production and disable staging:
```bash
# Use production buckets
INFLUXDB_BUCKET_TEMPERATURES=temperatures
INFLUXDB_BUCKET_WEATHER=weather
INFLUXDB_BUCKET_SPOTPRICE=spotprice
INFLUXDB_BUCKET_EMETERS=emeters
INFLUXDB_BUCKET_CHECKWATT=checkwatt_full_data
INFLUXDB_BUCKET_LOAD_CONTROL=load_control

# Disable staging mode (enable hardware control)
STAGING_MODE=false
```

```bash
# 2. Stop old system
# Disable old cron jobs: crontab -e
# Stop old scripts/services

# 3. Restart services to enable hardware control
sudo systemctl restart redhouse-*.timer

# 4. Monitor closely for the first 24 hours
journalctl -u redhouse-execute-program.service -f
```

### Fresh Installation

```bash
# 1. SSH to Raspberry Pi
ssh pi@<raspberry-pi-ip>

# 2. Copy deployment script
scp deployment/deploy.sh pi@<raspberry-pi-ip>:/tmp/

# 3. Run deployment script as root
sudo /tmp/deploy.sh
```

### Update Existing Installation

#### Smart Deployment (Recommended)

The smart deployment script automatically waits for the next optimal deployment window to avoid interfering with critical scheduled tasks.

```bash
# SSH to Raspberry Pi
ssh pi@<raspberry-pi-ip>

# Check when next safe window is
sudo /opt/redhouse/deployment/deploy_smart.sh --schedule

# Wait for next window and deploy automatically
sudo /opt/redhouse/deployment/deploy_smart.sh

# Or deploy immediately (emergency)
sudo /opt/redhouse/deployment/deploy_smart.sh --now
```

**Optimal deployment windows** (4 minutes each, 16 min/hour total):
- `:06-:09` - After aggregation, before next collection
- `:21-:24` - After aggregation, before next collection
- `:36-:39` - After aggregation, before next collection
- `:51-:54` - After aggregation, before next collection

**Avoids these critical times:**
- `:00, :15, :30, :45` - Heating program execution
- `:00, :05, :10, etc.` - 5-min aggregation + Shelly EM3 collection

#### Manual Deployment

```bash
# SSH to Raspberry Pi and run deployment script directly
ssh pi@<raspberry-pi-ip>
sudo /opt/redhouse/deployment/deploy.sh
```

The deployment script will:
1. Pull latest code from GitHub
2. Update Python dependencies
3. Run unit tests
4. Install/update systemd services
5. Restart all timers

**Note:** Manual deployment may briefly interrupt data collection if run during critical times.

## Manual Service Management

### View Service Status

```bash
# List all timers and their next run times
systemctl list-timers redhouse-*

# Check status of specific service
systemctl status redhouse-temperature.service
systemctl status redhouse-execute-program.timer
```

### View Logs

```bash
# Follow logs in real-time
journalctl -u redhouse-temperature.service -f
journalctl -u redhouse-execute-program.service -f

# View recent logs
journalctl -u redhouse-generate-program.service -n 50

# View logs from specific time
journalctl -u redhouse-weather.service --since "1 hour ago"

# View logs from all redhouse services
journalctl -u "redhouse-*" -f
```

### Start/Stop Services

```bash
# Stop all timers
sudo systemctl stop redhouse-*.timer

# Start all timers
sudo systemctl start redhouse-*.timer

# Restart specific timer
sudo systemctl restart redhouse-temperature.timer

# Disable a service (won't start on boot)
sudo systemctl disable redhouse-temperature.timer
```

### Manual Service Execution

```bash
# Run a service manually (without waiting for timer)
sudo systemctl start redhouse-temperature.service
sudo systemctl start redhouse-generate-program.service
```

## Configuration

### Environment Variables

Services read configuration from:
- `/opt/redhouse/.env` - InfluxDB credentials and URLs
- `/opt/redhouse/config/config.yaml` - System configuration

**Important:** After updating configuration files, restart affected services:

```bash
sudo systemctl restart redhouse-*.timer
```

### Service Files Location

All systemd service and timer files are located in:
- `/etc/systemd/system/redhouse-*.service`
- `/etc/systemd/system/redhouse-*.timer`

After modifying service files:

```bash
sudo systemctl daemon-reload
sudo systemctl restart redhouse-*.timer
```

## Monitoring

### Health Checks

Check if all services are running properly:

```bash
# View timer status
systemctl list-timers redhouse-*

# Check for failed services
systemctl --failed | grep redhouse

# View system journal for errors
journalctl -p err -u "redhouse-*" --since today
```

### Common Issues

**Service fails to start:**
```bash
# Check detailed error
systemctl status redhouse-<service>.service
journalctl -xe -u redhouse-<service>.service
```

**Python import errors:**
```bash
# Verify virtual environment
ls -la /opt/redhouse/venv/bin/python
# Reinstall dependencies
sudo -u pi /opt/redhouse/venv/bin/pip install -r /opt/redhouse/requirements.txt
```

**Permission errors:**
```bash
# Ensure correct ownership
sudo chown -R pi:pi /opt/redhouse
```

## Migrating from Crontab

To switch from the old crontab-based system:

1. Deploy systemd services (see above)
2. Verify services are running: `systemctl list-timers redhouse-*`
3. Monitor logs for 24 hours to ensure everything works
4. Disable old crontab entries: `crontab -e` (comment out redhouse lines)
5. Keep old scripts as backup for 1 week before removing

## Rollback

If you need to rollback to the previous version:

```bash
cd /opt/redhouse
sudo systemctl stop redhouse-*.timer
sudo -u pi git log --oneline -10  # Find commit to rollback to
sudo -u pi git reset --hard <commit-hash>
sudo /opt/redhouse/deployment/deploy.sh
```

## EVU-OFF Cycling

The geothermal heat pump requires EVU-OFF signal to be cycled periodically to prevent direct heating mode. This is now handled automatically by smart cycling logic in the pump controller:

- **Automatic cycling on state transitions**: EVU cycles when pump switches from ALE/ON to ON
- **Periodic cycling**: EVU cycles every 105 minutes while pump is ON (15-min safety margin before 120-min threshold)
- **State persistence**: Tracks ON time accumulation across restarts via `data/pump_state.json`

No separate timer service is needed - all cycling logic is integrated into `redhouse-execute-program`.

## Future Enhancements

- [ ] Health check service that monitors data freshness
- [ ] Automated alerts on service failures
- [ ] Grafana dashboard integration for service status

## Support

For issues or questions, check:
- Service logs: `journalctl -u redhouse-* -f`
- GitHub repository: https://github.com/vvaten/redhouse
- Modernization plan: `/opt/redhouse/MODERNIZATION_PLAN.md`
