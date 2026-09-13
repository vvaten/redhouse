#!/bin/bash
# Generate staging systemd service and timer files from production ones.
#
# Transforms /opt/redhouse -> /opt/redhouse-staging and
# redhouse-* -> redhouse-staging-* in unit names.
#
# Output goes to /etc/systemd/system/ but units are NOT enabled.
#
# Usage:
#   sudo deployment/generate_staging_systemd.sh

set -e

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/systemd"
GENERATED=0

echo "Generating staging systemd units from production templates..."

for src_file in "$SOURCE_DIR"/redhouse-*.service "$SOURCE_DIR"/redhouse-*.timer; do
    [ -f "$src_file" ] || continue

    filename=$(basename "$src_file")
    # redhouse-temperature.service -> redhouse-staging-temperature.service
    staging_name="${filename/redhouse-/redhouse-staging-}"

    # Use precise path replacement to avoid double-staging if run twice.
    # Replace /opt/redhouse/ (with trailing slash) and /opt/redhouse at end of line.
    sed \
        -e 's|/opt/redhouse/|/opt/redhouse-staging/|g' \
        -e 's|/opt/redhouse$|/opt/redhouse-staging|g' \
        -e 's|SyslogIdentifier=redhouse-|SyslogIdentifier=redhouse-staging-|g' \
        -e 's|Requires=redhouse-|Requires=redhouse-staging-|g' \
        -e 's|Description=RedHouse |Description=RedHouse Staging |g' \
        "$src_file" > "/etc/systemd/system/$staging_name"

    GENERATED=$((GENERATED + 1))
done

systemctl daemon-reload

echo "[OK] Generated $GENERATED staging systemd units (not enabled)"
echo ""
echo "Control with: sudo deployment/staging_timers.sh start|stop|status"
