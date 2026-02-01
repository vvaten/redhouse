# RedHouse Home Automation System

Intelligent home heating control system optimizing geothermal pump operation based on weather forecasts and electricity spot prices.

## Features

- **Smart Heating Optimization**: Generates daily heating schedules based on weather forecasts and electricity prices
- **Multi-source Data Collection**:
  - DS18B20 temperature sensors (1-wire)
  - FMI weather forecasts
  - Electricity spot prices
  - Solar/battery data from CheckWatt
- **Hardware Control**:
  - Geothermal heat pump control via I2C
  - Shelly relay integration
- **Data Visualization**: InfluxDB + Grafana dashboards
- **Extensible**: Ready for garage heating, EV charging, and load balancing

## Architecture

```
Raspberry Pi (Data Collection & Control)
    |
    +-- Temperature Sensors (1-wire DS18B20)
    +-- Geothermal Pump (I2C control)
    +-- External APIs (Weather, Prices)
    |
    v
NAS (Docker)
    +-- InfluxDB (Time-series data)
    +-- Grafana (Visualization)
```

## Project Structure

```
redhouse/
├── .env                    # Environment variables (not in git, copy from .env.example)
├── src/
│   ├── data_collection/    # Sensor readers, API clients
│   ├── control/            # Heating optimizer, pump controller
│   ├── common/             # Shared utilities (config, logging, influx)
│   ├── aggregation/        # Data aggregation pipelines
│   └── simulation/         # Backtesting tools
├── config/
│   └── config.yaml         # System configuration (copy from config.yaml.example)
├── tests/                  # Unit and integration tests
├── deployment/             # Systemd services, deployment scripts
└── docs/                   # Documentation
```

## Installation

### Prerequisites

- Raspberry Pi (3/4/5) with Raspberry Pi OS
- Python 3.9+
- InfluxDB 2.x running on NAS
- Hardware: DS18B20 sensors, I2C relay for pump control

### Setup on Raspberry Pi

1. **Clone repository**
```bash
cd /opt
sudo git clone <your-repo-url> redhouse
cd redhouse
sudo chown -R pi:pi /opt/redhouse
```

2. **Create virtual environment**
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

3. **Configure environment**
```bash
cp .env.example .env
nano .env  # Edit with your credentials
```

4. **Configure system settings**
```bash
cp config/config.yaml.example config/config.yaml
nano config/config.yaml  # Adjust heating curve, schedules, etc.
```

5. **Create log directory**
```bash
sudo mkdir -p /var/log/redhouse
sudo chown pi:pi /var/log/redhouse
```

6. **Test configuration**
```bash
source venv/bin/activate
python -c "from src.common.config import get_config; print(get_config().influxdb_url)"
```

## Development

### Quality Checks (Required Before Commit)

**IMPORTANT:** Always run comprehensive checks before committing:

```bash
source venv/bin/activate
python -u scripts/run_all_checks.py
```

This runs:
- Code formatting (black)
- Linting (ruff)
- Type checking (mypy)
- Unit tests (pytest)
- Code quality metrics
- Code coverage

**Quick options:**
```bash
# Auto-fix formatting and linting
python -u scripts/run_all_checks.py --fix

# Skip coverage for faster checks
python -u scripts/run_all_checks.py --quick

# Format/lint only, skip tests
python -u scripts/run_all_checks.py --no-tests
```

### Running Tests

**Unit tests** (fast, safe - mock hardware and InfluxDB):
```bash
source venv/bin/activate
pytest tests/unit/ -v
```

**Integration tests** (write to test buckets):
```bash
# Create test buckets first (see below)
pytest tests/integration/ -v
```

**With coverage**:
```bash
pytest tests/ --cov=src --cov-report=html
```

**Specific test file**:
```bash
pytest tests/unit/test_heating_optimizer.py -v
```

### Test Buckets Setup

Integration tests require test buckets in InfluxDB:

```bash
# Create test buckets (via InfluxDB UI or CLI)
influx bucket create -n temperatures_test -o area51 -r 30d
influx bucket create -n weather_test -o area51 -r 30d
influx bucket create -n spotprice_test -o area51 -r 30d
influx bucket create -n emeters_test -o area51 -r 30d
influx bucket create -n checkwatt_full_data_test -o area51 -r 30d
influx bucket create -n load_control_test -o area51 -r 30d
```

Configure your `.env` to use test buckets when developing.

### Running Individual Components

```bash
# Temperature collection
python -m src.data_collection.temperature

# Weather data
python -m src.data_collection.weather

# Generate heating program
python -m src.control.heating_optimizer
```

## Deployment

See [deployment/README.md](deployment/README.md) for systemd service setup and deployment instructions.

## Configuration

### Environment Variables (.env)

Key environment variables:
- `INFLUXDB_URL`: InfluxDB server URL
- `INFLUXDB_TOKEN`: InfluxDB authentication token
- `INFLUXDB_ORG`: Organization name
- `CHECKWATT_USERNAME`: CheckWatt API username
- `CHECKWATT_PASSWORD`: CheckWatt API password
- `WEATHER_LATLON`: Location for weather forecasts

See [.env.example](.env.example) for complete list.

### Configuration File (config.yaml)

Main configuration options:
- **heating.curve**: Temperature-to-heating-hours mapping
- **heating.evuoff_threshold_price**: Price threshold for blocking hot water heating
- **data_collection**: Collection intervals and settings
- **sensor_mapping**: Sensor ID to display name mapping

See [config/config.yaml.example](config/config.yaml.example) for details.

## Monitoring

### Logs

```bash
# Application logs
tail -f /var/log/redhouse/collector.log

# Systemd logs
journalctl -u redhouse-* -f
```

### Grafana Dashboards

Import dashboards from `grafana/dashboards/` directory.

## Troubleshooting

### Temperature sensors not reading

```bash
# Check 1-wire devices
ls /sys/bus/w1/devices/

# Check kernel module
lsmod | grep w1
```

### I2C not working

```bash
# Enable I2C
sudo raspi-config  # Interface Options -> I2C -> Enable

# Check I2C devices
i2cdetect -y 1
```

### InfluxDB connection issues

```bash
# Test connection
curl -v http://192.168.1.164:8086/health

# Verify token
influx auth list --host http://192.168.1.164:8086
```

## Future Enhancements

- [ ] Multi-load support (garage heating, EV charging)
- [ ] Load balancing algorithm
- [ ] Grafana control buttons (manual overrides)
- [ ] Historical simulation and strategy comparison
- [ ] Alerting system (sensor failures, program errors)
- [ ] REST API for external integrations

## License

Private project - All rights reserved

## Credits

Weather data: Finnish Meteorological Institute (FMI)
Spot prices: spot-hinta.fi API
