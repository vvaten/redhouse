#!/bin/bash
# RedHouse Deployment Script for Raspberry Pi
# This script deploys or updates the RedHouse home automation system

set -e

DEPLOY_DIR=/opt/redhouse
REPO_URL=https://github.com/vvaten/redhouse.git
VENV_DIR=$DEPLOY_DIR/venv

echo "=================================================="
echo "RedHouse Deployment Script"
echo "=================================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)"
    exit 1
fi

# Initial setup or update?
if [ -d "$DEPLOY_DIR/.git" ]; then
    echo "[UPDATE] Updating existing installation..."
    cd $DEPLOY_DIR

    # Stash any local changes
    sudo -u pi git stash

    # Pull latest changes
    sudo -u pi git pull origin main

    echo "[OK] Git repository updated"
else
    echo "[INSTALL] Fresh installation..."

    # Create deployment directory
    mkdir -p $DEPLOY_DIR
    chown pi:pi $DEPLOY_DIR

    # Clone repository
    sudo -u pi git clone $REPO_URL $DEPLOY_DIR
    cd $DEPLOY_DIR

    echo "[OK] Repository cloned"
fi

# Create/update virtual environment
echo ""
echo "Setting up Python virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    sudo -u pi python3 -m venv $VENV_DIR
    echo "[OK] Virtual environment created"
else
    echo "[OK] Virtual environment exists"
fi

# Install/update dependencies
echo ""
echo "Installing Python dependencies..."
sudo -u pi $VENV_DIR/bin/pip install --upgrade pip
sudo -u pi $VENV_DIR/bin/pip install -r requirements.txt
echo "[OK] Dependencies installed"

# Run tests (unit tests only, skip integration tests)
echo ""
echo "Running unit tests..."
sudo -u pi $VENV_DIR/bin/pytest tests/unit/ -v --tb=short
if [ $? -eq 0 ]; then
    echo "[OK] All tests passed"
else
    echo "[FAIL] Tests failed - deployment aborted"
    exit 1
fi

# Install systemd services
echo ""
echo "Installing systemd services..."
cp deployment/systemd/*.service /etc/systemd/system/
cp deployment/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
echo "[OK] Systemd services installed"

# Enable and start timers
echo ""
echo "Enabling systemd timers..."
TIMERS=(
    "redhouse-temperature"
    "redhouse-weather"
    "redhouse-spot-prices"
    "redhouse-checkwatt"
    "redhouse-solar-prediction"
    "redhouse-generate-program"
    "redhouse-execute-program"
    "redhouse-evu-cycle"
)

for timer in "${TIMERS[@]}"; do
    systemctl enable $timer.timer
    systemctl restart $timer.timer
    echo "[OK] Enabled $timer.timer"
done

# Show status
echo ""
echo "=================================================="
echo "Deployment Complete!"
echo "=================================================="
echo ""
echo "Service Status:"
systemctl list-timers redhouse-*
echo ""
echo "To view logs:"
echo "  journalctl -u redhouse-temperature.service -f"
echo "  journalctl -u redhouse-execute-program.service -f"
echo ""
echo "To stop all services:"
echo "  sudo systemctl stop redhouse-*.timer"
echo ""
