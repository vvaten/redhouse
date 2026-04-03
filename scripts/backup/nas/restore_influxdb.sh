#!/bin/bash
# Restore InfluxDB from a native backup.
#
# Requires:
#   - Docker access (run as root)
#   - Operator token with system-level permissions
#   - /backups volume mounted in influxdb2 container
#
# Usage:
#   ./restore_influxdb.sh /share/Backups/redhouse/nas/2026-04-03_033000/influxdb --dry-run
#   ./restore_influxdb.sh /share/Backups/redhouse/nas/2026-04-03_033000/influxdb
#   ./restore_influxdb.sh /share/Backups/redhouse/nas/latest/influxdb

set -euo pipefail

BACKUP_DIR="$1"
DRY_RUN="${2:-}"

CONTAINER="influxdb2"
INFLUX_HOST="http://localhost:8086"

OPERATOR_TOKEN_FILE="/share/Backups/redhouse/nas/.operator_token"

if [ ! -f "$OPERATOR_TOKEN_FILE" ]; then
    echo "ERROR: Operator token file not found: $OPERATOR_TOKEN_FILE"
    exit 1
fi

OPERATOR_TOKEN=$(cat "$OPERATOR_TOKEN_FILE")

if [ ! -d "$BACKUP_DIR" ]; then
    echo "ERROR: Backup directory not found: $BACKUP_DIR"
    exit 1
fi

FILE_COUNT=$(ls -1 "$BACKUP_DIR" 2>/dev/null | wc -l)
BACKUP_SIZE=$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)

echo "=== InfluxDB Restore ==="
echo "  Source: $BACKUP_DIR"
echo "  Files: $FILE_COUNT"
echo "  Size: $BACKUP_SIZE"

if [ "$DRY_RUN" = "--dry-run" ]; then
    echo "  DRY-RUN: would restore $FILE_COUNT files ($BACKUP_SIZE)"
    exit 0
fi

# Map host path to container path
# Host: /share/Backups/redhouse/nas/... -> Container: /backups/...
SNAPSHOT_NAME=$(basename "$(dirname "$BACKUP_DIR")")
CONTAINER_PATH="/backups/$SNAPSHOT_NAME/influxdb"

echo ""
echo "WARNING: This will restore data into InfluxDB."
echo "  - Existing data in matching buckets may be overwritten"
echo "  - Container path: $CONTAINER_PATH"
echo ""
read -p "Continue? [y/N] " CONFIRM
if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
    echo "Aborted."
    exit 0
fi

# Check container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    echo "ERROR: Container $CONTAINER is not running"
    exit 1
fi

echo "Restoring..."
START_TIME=$(date +%s)

docker exec "$CONTAINER" influx restore "$CONTAINER_PATH" \
    --host "$INFLUX_HOST" \
    --token "$OPERATOR_TOKEN" \
    --full \
    2>&1

EXIT_CODE=$?
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

if [ $EXIT_CODE -ne 0 ]; then
    echo "ERROR: influx restore failed (exit $EXIT_CODE)"
    exit 1
fi

echo ""
echo "=== Restore Complete (${DURATION}s) ==="
echo "Verify data in InfluxDB UI or via Flux queries."
