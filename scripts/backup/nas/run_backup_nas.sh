#!/bin/sh
# NAS backup orchestrator: InfluxDB + Grafana.
#
# Runs on the Asustor NAS as a scheduled job (03:30 daily).
# Creates a dated snapshot directory, runs InfluxDB native backup
# and Grafana API export, writes a manifest, and cleans up old snapshots.
#
# Usage:
#   ./run_backup_nas.sh
#   ./run_backup_nas.sh --dry-run

# Ensure Asustor system binaries are in PATH (sudo resets PATH)
PATH="/usr/builtin/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export PATH

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_ROOT="/share/Backups/redhouse/nas"
DRY_RUN="${1:-}"

TIMESTAMP=$(date -u +"%Y-%m-%d_%H%M%S")
SNAPSHOT_DIR="$BACKUP_ROOT/$TIMESTAMP"

# Retention: delete snapshots older than 114 days
WEEKLY_RETENTION_DAYS=114

echo "=== NAS Backup [$TIMESTAMP] ==="

# Create snapshot directory
mkdir -p "$SNAPSHOT_DIR/influxdb"
mkdir -p "$SNAPSHOT_DIR/grafana"

FAILURES=""

# Step 1: InfluxDB backup
echo "[1/3] InfluxDB backup..."
if sh "$SCRIPT_DIR/backup_influxdb.sh" "$SNAPSHOT_DIR/influxdb" "$DRY_RUN"; then
    INFLUX_STATUS="ok"
else
    INFLUX_STATUS="failed"
    FAILURES="$FAILURES influxdb"
    echo "  ERROR: InfluxDB backup failed"
fi

# Step 2: Grafana backup
echo "[2/3] Grafana backup..."
if sh "$SCRIPT_DIR/backup_grafana.sh" "$SNAPSHOT_DIR/grafana" "$DRY_RUN"; then
    GRAFANA_STATUS="ok"
else
    GRAFANA_STATUS="failed"
    FAILURES="$FAILURES grafana"
    echo "  ERROR: Grafana backup failed"
fi

# Step 3: Write manifest
echo "[3/3] Writing manifest..."
INFLUX_SIZE=$(du -sh "$SNAPSHOT_DIR/influxdb" 2>/dev/null | cut -f1 || echo "0")
INFLUX_FILES=$(ls -1 "$SNAPSHOT_DIR/influxdb" 2>/dev/null | wc -l || echo "0")
GRAFANA_SIZE=$(du -sh "$SNAPSHOT_DIR/grafana" 2>/dev/null | cut -f1 || echo "0")
GRAFANA_DASHBOARDS=$(ls -1 "$SNAPSHOT_DIR/grafana/dashboards" 2>/dev/null | wc -l || echo "0")

cat > "$SNAPSHOT_DIR/backup_manifest.json" << MANIFEST_EOF
{
  "timestamp": "$(date -u +"%Y-%m-%dT%H:%M:%S+00:00")",
  "hostname": "$(hostname)",
  "snapshot": "$TIMESTAMP",
  "influxdb": {
    "status": "$INFLUX_STATUS",
    "files": $INFLUX_FILES,
    "size": "$INFLUX_SIZE"
  },
  "grafana": {
    "status": "$GRAFANA_STATUS",
    "dashboards": $GRAFANA_DASHBOARDS,
    "size": "$GRAFANA_SIZE"
  },
  "errors": "$(echo $FAILURES | xargs)"
}
MANIFEST_EOF

# Update latest symlink
if [ "$DRY_RUN" != "--dry-run" ]; then
    cd "$BACKUP_ROOT" && rm -f latest && ln -s "$TIMESTAMP" latest
    echo "  Updated latest -> $TIMESTAMP"
fi

# Step 4: Clean up old snapshots
echo "[4/4] Cleaning up old snapshots..."
if [ "$DRY_RUN" != "--dry-run" ]; then
    # Compute cutoff date (POSIX-compatible using awk)
    CUTOFF_DELETE=$(awk "BEGIN { print strftime(\"%Y-%m-%d\", systime() - $WEEKLY_RETENTION_DAYS * 86400) }" 2>/dev/null || echo "")

    if [ -n "$CUTOFF_DELETE" ]; then
        DELETED=0
        for DIR in "$BACKUP_ROOT"/????-??-??_??????; do
            [ -d "$DIR" ] || continue
            DIR_NAME=$(basename "$DIR")
            # Extract date part (first 10 chars) using cut
            DIR_DATE=$(echo "$DIR_NAME" | cut -c1-10)

            if expr "$DIR_DATE" \< "$CUTOFF_DELETE" > /dev/null 2>&1; then
                rm -rf "$DIR"
                echo "  Deleted old snapshot: $DIR_NAME"
                DELETED=$((DELETED + 1))
            fi
        done

        if [ $DELETED -gt 0 ]; then
            echo "  Cleaned up $DELETED old snapshot(s)"
        else
            echo "  No old snapshots to clean up"
        fi
    else
        echo "  WARNING: Could not compute cleanup cutoff date"
    fi
else
    echo "  DRY-RUN: skipping cleanup"
fi

# Summary
TOTAL_SIZE=$(du -sh "$SNAPSHOT_DIR" 2>/dev/null | cut -f1 || echo "0")
echo ""
echo "=== NAS Backup Complete ==="
echo "  Snapshot: $TIMESTAMP"
echo "  Size: $TOTAL_SIZE"

if [ -n "$FAILURES" ]; then
    echo "  FAILURES:$FAILURES"
    exit 1
fi

echo "  Status: OK"
exit 0
