#!/bin/bash
# RedHouse Production Deployment Script
#
# Deploys latest main branch to /opt/redhouse with smart deploy timing.
# Assumes code has already been tested in staging.
#
# Usage:
#   sudo deployment/deploy_production.sh          # Wait for safe window
#   sudo deployment/deploy_production.sh --now     # Deploy immediately

set -e

DEPLOY_DIR=/opt/redhouse
VENV_DIR="$DEPLOY_DIR/venv"
REPO_URL=git@github.com:vvaten/redhouse.git

# Smart deploy window configuration
# IMPORTANT: Keep in sync with systemd timer schedules in deployment/systemd/*.timer
OPTIMAL_WINDOWS=(7 22 37 52)
WINDOW_START_OFFSET=10

echo "=================================================="
echo "RedHouse Production Deployment"
echo "=================================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)"
    exit 1
fi

# --- Smart deploy window functions ---

in_optimal_window() {
    local current_minute
    current_minute=$(date +%M | sed 's/^0*//')
    local current_second
    current_second=$(date +%S | sed 's/^0*//')
    [ -z "$current_minute" ] && current_minute=0
    [ -z "$current_second" ] && current_second=0

    for window_start in "${OPTIMAL_WINDOWS[@]}"; do
        local window_end=$((window_start + 2))
        if [ "$current_minute" -eq "$window_start" ] && [ "$current_second" -ge "$WINDOW_START_OFFSET" ]; then
            return 0
        fi
        if [ "$current_minute" -gt "$window_start" ] && [ "$current_minute" -lt "$window_end" ]; then
            return 0
        fi
        if [ "$current_minute" -eq "$window_end" ] && [ "$current_second" -le 30 ]; then
            return 0
        fi
    done
    return 1
}

get_next_window() {
    local current_minute
    current_minute=$(date +%M | sed 's/^0*//')
    [ -z "$current_minute" ] && current_minute=0
    for window_start in "${OPTIMAL_WINDOWS[@]}"; do
        if [ "$current_minute" -lt "$window_start" ]; then
            echo "$window_start"
            return
        fi
    done
    echo "${OPTIMAL_WINDOWS[0]}"
}

wait_for_window() {
    if in_optimal_window; then
        echo "[OK] Currently in safe deployment window"
        return
    fi

    local next_window
    next_window=$(get_next_window)
    local current_minute
    current_minute=$(date +%M | sed 's/^0*//')
    local current_second
    current_second=$(date +%S | sed 's/^0*//')
    [ -z "$current_minute" ] && current_minute=0
    [ -z "$current_second" ] && current_second=0

    local minutes_to_wait
    if [ "$current_minute" -lt "$next_window" ]; then
        minutes_to_wait=$((next_window - current_minute))
    else
        minutes_to_wait=$((60 - current_minute + next_window))
    fi
    local wait_seconds=$((minutes_to_wait * 60 - current_second + WINDOW_START_OFFSET))

    echo "Waiting for safe deployment window (${wait_seconds}s)..."
    echo "Safe windows: :07, :22, :37, :52"
    echo "Press Ctrl+C to cancel"
    echo ""

    local remaining=$wait_seconds
    while [ $remaining -gt 0 ]; do
        printf "\rTime remaining: %02d:%02d " $((remaining / 60)) $((remaining % 60))
        sleep 1
        remaining=$((remaining - 1))
    done
    echo ""
    echo "[OK] Safe deployment window reached"
}

# --- Parse arguments ---

DEPLOY_NOW=false
if [ "${1}" = "--now" ]; then
    DEPLOY_NOW=true
fi

# --- Initial setup or update ---

if [ -d "$DEPLOY_DIR/.git" ]; then
    echo "[UPDATE] Updating existing production installation..."
    cd "$DEPLOY_DIR"

    # Record pre-deploy commit for rollback
    PRE_DEPLOY_COMMIT=$(sudo -u pi git rev-parse HEAD)
    echo "Pre-deploy commit: $PRE_DEPLOY_COMMIT"

    # Stash local changes if any
    STASH_OUTPUT=$(sudo -u pi git stash 2>&1)
    if echo "$STASH_OUTPUT" | grep -q "Saved working directory"; then
        echo "[WARN] Local changes stashed: $STASH_OUTPUT"
        echo "       Recover with: git stash pop"
    fi

    sudo -u pi git fetch origin main
    echo "[OK] Fetched latest from origin/main"
else
    echo "[INSTALL] Fresh production installation..."
    mkdir -p "$DEPLOY_DIR"
    chown pi:pi "$DEPLOY_DIR"
    sudo -u pi git clone "$REPO_URL" "$DEPLOY_DIR"
    cd "$DEPLOY_DIR"
    PRE_DEPLOY_COMMIT="(fresh install)"
    echo "[OK] Repository cloned"
fi

# --- Wait for safe window (or skip with --now) ---

if [ "$DEPLOY_NOW" = true ]; then
    echo "[WARN] Deploying immediately (--now flag, skipping window check)"
else
    wait_for_window
fi

# --- Apply code update (fast-forward only) ---

echo ""
echo "Applying code update..."
if ! sudo -u pi git merge --ff-only origin/main; then
    echo "[FAIL] Fast-forward merge failed (local commits diverged from main)"
    echo "Resolve manually: git reset --hard origin/main"
    exit 1
fi
echo "[OK] Code updated to latest main"

# --- Create/update virtual environment ---

echo ""
echo "Setting up Python virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    sudo -u pi python3 -m venv "$VENV_DIR"
    echo "[OK] Virtual environment created"
else
    echo "[OK] Virtual environment exists"
fi

echo "Installing Python dependencies..."
sudo -u pi "$VENV_DIR/bin/pip" install --quiet --upgrade pip
sudo -u pi "$VENV_DIR/bin/pip" install --quiet -r requirements.txt
echo "[OK] Dependencies installed"

# --- Validate configuration ---

echo ""
echo "Validating configuration..."

if [ ! -f "$DEPLOY_DIR/.env" ]; then
    echo "[FAIL] No .env file found at $DEPLOY_DIR/.env"
    echo "Copy .env.example and configure: cp .env.example .env"
    exit 1
fi

if [ ! -f "$DEPLOY_DIR/config/sensors.yaml" ]; then
    echo "[FAIL] No config/sensors.yaml found"
    echo "Copy sensors.yaml.example and configure with real sensor IDs"
    exit 1
fi

echo "[OK] .env and config/sensors.yaml exist"

# --- Create log directory ---

mkdir -p /var/log/redhouse
chown pi:pi /var/log/redhouse

# --- Install and restart systemd services ---

echo ""
echo "Installing systemd services..."
cp deployment/systemd/*.service /etc/systemd/system/
cp deployment/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
echo "[OK] Systemd services installed"

echo ""
echo "Restarting systemd timers..."
TIMERS=(
    "redhouse-temperature"
    "redhouse-weather"
    "redhouse-spot-prices"
    "redhouse-checkwatt"
    "redhouse-shelly-em3"
    "redhouse-windpower"
    "redhouse-aggregate-emeters-5min"
    "redhouse-aggregate-analytics-15min"
    "redhouse-aggregate-analytics-1hour"
    "redhouse-solar-prediction"
    "redhouse-generate-program"
    "redhouse-execute-program"
    "redhouse-health-check"
    "redhouse-backup"
)

for timer in "${TIMERS[@]}"; do
    systemctl enable "$timer.timer" 2>/dev/null
    systemctl restart "$timer.timer"
    echo "  [OK] $timer.timer"
done

# --- Set up Grafana alerts ---

echo ""
echo "Setting up Grafana alerts..."
if sudo -u pi "$VENV_DIR/bin/python" -u deployment/setup_grafana_alerts.py --env production 2>/dev/null; then
    echo "[OK] Grafana alerts deployed"
else
    echo "[WARN] Grafana alert setup failed (non-fatal)"
fi

# --- Done ---

echo ""
echo "=================================================="
echo "Production Deployment Complete!"
echo "=================================================="
echo ""
echo "Pre-deploy commit: ${PRE_DEPLOY_COMMIT}"
echo "Current commit:    $(sudo -u pi git rev-parse HEAD)"
echo ""
echo "Rollback: sudo -u pi git reset --hard ${PRE_DEPLOY_COMMIT}"
echo ""
systemctl list-timers redhouse-* --no-pager
echo ""
echo "Monitor: journalctl -u \"redhouse-*\" -f"
echo "Stop:    sudo systemctl stop redhouse-*.timer"
