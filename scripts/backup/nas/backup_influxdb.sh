#!/bin/bash
# Back up InfluxDB using native influx backup command.
#
# Requires:
#   - Docker access (run as root)
#   - Operator token with system-level permissions
#   - /backups volume mounted in influxdb2 container
#
# Usage:
#   ./backup_influxdb.sh /share/Backups/redhouse/nas/2026-04-03_033000/influxdb
#   ./backup_influxdb.sh /share/Backups/redhouse/nas/2026-04-03_033000/influxdb --dry-run

set -euo pipefail

BACKUP_DIR="$1"
DRY_RUN="${2:-}"

CONTAINER="influxdb2"
INFLUX_HOST="http://localhost:8086"

# Operator token -- required for influx backup (all-access tokens are not enough)
# This token was created via: influxd recovery auth create-operator
OPERATOR_TOKEN_FILE="/share/Backups/redhouse/nas/.operator_token"

if [ ! -f "$OPERATOR_TOKEN_FILE" ]; then
    echo "ERROR: Operator token file not found: $OPERATOR_TOKEN_FILE"
    echo "Create it with: echo 'your-token' > $OPERATOR_TOKEN_FILE && chmod 600 $OPERATOR_TOKEN_FILE"
    exit 1
fi

OPERATOR_TOKEN=$(cat "$OPERATOR_TOKEN_FILE")

if [ -z "$OPERATOR_TOKEN" ]; then
    echo "ERROR: Operator token file is empty"
    exit 1
fi

# Map host backup dir to container path
# Host: /share/Backups/redhouse/nas/... -> Container: /backups/...
CONTAINER_PATH="/backups/$(basename "$(dirname "$BACKUP_DIR")")/influxdb"

echo "  InfluxDB backup -> $BACKUP_DIR"

if [ "$DRY_RUN" = "--dry-run" ]; then
    echo "  DRY-RUN: would run influx backup to $CONTAINER_PATH"
    exit 0
fi

# Check container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    echo "ERROR: Container $CONTAINER is not running"
    exit 1
fi

# Run backup
START_TIME=$(date +%s)

docker exec "$CONTAINER" influx backup "$CONTAINER_PATH" \
    --host "$INFLUX_HOST" \
    --token "$OPERATOR_TOKEN" \
    2>&1

EXIT_CODE=$?
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

if [ $EXIT_CODE -ne 0 ]; then
    echo "ERROR: influx backup failed (exit $EXIT_CODE)"
    exit 1
fi

# Verify backup directory exists and has files
FILE_COUNT=$(ls -1 "$BACKUP_DIR" 2>/dev/null | wc -l)
BACKUP_SIZE=$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)

echo "  InfluxDB backup complete: $FILE_COUNT files, $BACKUP_SIZE, ${DURATION}s"
