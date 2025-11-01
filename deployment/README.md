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
| `redhouse-execute-program` | Every 15 min (:00, :15, :30, :45) | Execute heating program commands |
| `redhouse-evu-cycle` | Every 2 hours at :23 | Cycle EVU-OFF to prevent direct heating mode |

## Deployment

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

```bash
# SSH to Raspberry Pi and run deployment script
ssh pi@<raspberry-pi-ip>
sudo /opt/redhouse/deployment/deploy.sh
```

The deployment script will:
1. Pull latest code from GitHub
2. Update Python dependencies
3. Run unit tests
4. Install/update systemd services
5. Restart all timers

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

## Future Enhancements

- [ ] Smart EVU-OFF cycling based on actual pump ON time (not fixed 2-hour schedule)
- [ ] Health check service that monitors data freshness
- [ ] Automated alerts on service failures
- [ ] Grafana dashboard integration for service status

## Support

For issues or questions, check:
- Service logs: `journalctl -u redhouse-* -f`
- GitHub repository: https://github.com/vvaten/redhouse
- Modernization plan: `/opt/redhouse/MODERNIZATION_PLAN.md`
