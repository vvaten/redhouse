#!/bin/bash
# RedHouse Staging Deployment Script
#
# Deploys code to /opt/redhouse-staging for testing.
# Does NOT enable systemd timers (manual testing only).
# Copies latest production data to staging buckets.
#
# Usage:
#   sudo deployment/deploy_staging.sh                    # Deploy main
#   sudo deployment/deploy_staging.sh feature/my-change  # Deploy branch

set -e

DEPLOY_DIR=/opt/redhouse-staging
VENV_DIR=$DEPLOY_DIR/venv
REPO_URL=git@github.com:vvaten/redhouse.git
BRANCH="${1:-main}"

echo "=================================================="
echo "RedHouse Staging Deployment"
echo "=================================================="
echo "Branch: $BRANCH"
echo "Target: $DEPLOY_DIR"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)"
    exit 1
fi

# --- Initial setup or update ---

if [ -d "$DEPLOY_DIR/.git" ]; then
    echo "[UPDATE] Updating staging installation..."
    cd "$DEPLOY_DIR"

    # Stash local changes if any
    local_stash=$(sudo -u pi git stash 2>&1) || true
    if echo "$local_stash" | grep -q "Saved working directory"; then
        echo "[WARN] Local changes stashed: $local_stash"
    fi

    sudo -u pi git fetch origin
    # Reset to requested branch to avoid merge conflicts in staging
    sudo -u pi git checkout "$BRANCH" 2>/dev/null || sudo -u pi git checkout -b "$BRANCH" "origin/$BRANCH"
    sudo -u pi git reset --hard "origin/$BRANCH"
    echo "[OK] Reset to origin/$BRANCH"
else
    echo "[INSTALL] Fresh staging installation..."
    mkdir -p "$DEPLOY_DIR"
    chown pi:pi "$DEPLOY_DIR"
    sudo -u pi git clone "$REPO_URL" "$DEPLOY_DIR"
    cd "$DEPLOY_DIR"
    if [ "$BRANCH" != "main" ]; then
        sudo -u pi git checkout -b "$BRANCH" "origin/$BRANCH"
    fi
    echo "[OK] Repository cloned (branch: $BRANCH)"
fi

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
    echo ""
    echo "Create it manually with staging bucket names:"
    echo "  cp /opt/redhouse/.env $DEPLOY_DIR/.env"
    echo "  nano $DEPLOY_DIR/.env"
    echo "  # Set STAGING_MODE=true"
    echo "  # Change all bucket names to *_staging"
    exit 1
fi

# Verify STAGING_MODE is enabled
if ! grep -q "STAGING_MODE=true" "$DEPLOY_DIR/.env"; then
    echo "[FAIL] STAGING_MODE is not set to true in $DEPLOY_DIR/.env"
    echo "This would write to production buckets from staging!"
    echo "Fix: Set STAGING_MODE=true in $DEPLOY_DIR/.env"
    exit 1
fi

if [ ! -f "$DEPLOY_DIR/config/sensors.yaml" ]; then
    if [ -f "/opt/redhouse/config/sensors.yaml" ]; then
        echo "[INFO] Copying sensors.yaml from production..."
        cp /opt/redhouse/config/sensors.yaml "$DEPLOY_DIR/config/sensors.yaml"
        chown pi:pi "$DEPLOY_DIR/config/sensors.yaml"
        echo "[OK] config/sensors.yaml copied"
    else
        echo "[FAIL] No config/sensors.yaml found"
        echo "Copy sensors.yaml.example and configure with real sensor IDs"
        exit 1
    fi
fi

echo "[OK] .env (STAGING_MODE=true) and config/sensors.yaml exist"

# --- Run unit tests ---

echo ""
echo "Running unit tests..."
if sudo -u pi "$VENV_DIR/bin/pytest" tests/unit/ -v --tb=short; then
    echo "[OK] All tests passed"
else
    echo "[FAIL] Tests failed - deployment aborted"
    exit 1
fi

# --- Create log directory ---

mkdir -p /var/log/redhouse
chown pi:pi /var/log/redhouse

# --- Generate staging systemd units (installed but NOT enabled) ---

echo ""
"$DEPLOY_DIR/deployment/generate_staging_systemd.sh"

# --- Copy latest production data to staging buckets ---

echo ""
echo "Copying latest production data to staging buckets..."
if sudo -u pi "$VENV_DIR/bin/python" -u deployment/copy_production_to_staging.py --days 2; then
    echo "[OK] Staging data refreshed"
else
    echo "[WARN] Data copy failed (non-fatal, staging may have stale data)"
fi

# --- Done ---

echo ""
echo "=================================================="
echo "Staging Deployment Complete!"
echo "=================================================="
echo ""
echo "To test manually:"
echo "  cd $DEPLOY_DIR"
echo "  source venv/bin/activate"
echo "  python collect_temperatures.py --dry-run --verbose"
echo "  python generate_heating_program_v2.py --dry-run"
echo ""
echo "Staging timers (not enabled by default):"
echo "  sudo deployment/staging_timers.sh start             # Start all"
echo "  sudo deployment/staging_timers.sh start temperature  # Start one"
echo "  sudo deployment/staging_timers.sh stop               # Stop all"
echo "  sudo deployment/staging_timers.sh status             # Show active"
echo ""
echo "Staging data is from production (last 2 days)."
echo "To refresh: python deployment/copy_production_to_staging.py --days 1"
