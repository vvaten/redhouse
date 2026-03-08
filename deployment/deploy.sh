#!/bin/bash
# RedHouse Deployment Script for Raspberry Pi
# Phase 1: Git pull/clone, then hand off to deploy_install.sh
# This two-script design ensures deploy_install.sh always runs
# the latest version from git, not a stale in-memory copy.

set -e

DEPLOY_DIR=/opt/redhouse
REPO_URL=git@github.com:vvaten/redhouse.git

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

# Hand off to the freshly pulled install script
echo ""
echo "Running install script..."
exec "$DEPLOY_DIR/deployment/deploy_install.sh"
