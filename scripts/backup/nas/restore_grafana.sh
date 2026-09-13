#!/bin/sh
# Restore Grafana dashboards and alert configuration from backup.
#
# Imports dashboard JSON files and alert rules via REST API.
# Runs on the NAS where Grafana is localhost.
#
# Usage:
#   ./restore_grafana.sh /share/Backups/redhouse/nas/2026-04-03_033000/grafana --dry-run
#   ./restore_grafana.sh /share/Backups/redhouse/nas/2026-04-03_033000/grafana
#   ./restore_grafana.sh /share/Backups/redhouse/nas/latest/grafana

set -eu

BACKUP_DIR="$1"
DRY_RUN="${2:-}"

GRAFANA_URL="http://localhost:3000"
GRAFANA_API_KEY_FILE="/share/Backups/redhouse/nas/.grafana_api_key"

if [ ! -f "$GRAFANA_API_KEY_FILE" ]; then
    echo "ERROR: Grafana API key file not found: $GRAFANA_API_KEY_FILE"
    exit 1
fi

GRAFANA_API_KEY=$(cat "$GRAFANA_API_KEY_FILE")
AUTH_HEADER="Authorization: Bearer $GRAFANA_API_KEY"

if [ ! -d "$BACKUP_DIR" ]; then
    echo "ERROR: Backup directory not found: $BACKUP_DIR"
    exit 1
fi

DASHBOARD_DIR="$BACKUP_DIR/dashboards"
DASHBOARD_COUNT=$(ls -1 "$DASHBOARD_DIR"/*.json 2>/dev/null | wc -l)

echo "=== Grafana Restore ==="
echo "  Source: $BACKUP_DIR"
echo "  Dashboards: $DASHBOARD_COUNT"

if [ "$DRY_RUN" = "--dry-run" ]; then
    echo "  DRY-RUN: would restore $DASHBOARD_COUNT dashboards"
    # Test API connectivity
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "$AUTH_HEADER" "$GRAFANA_URL/api/health")
    echo "  Grafana API health: HTTP $HTTP_CODE"
    exit 0
fi

echo ""
echo "WARNING: This will overwrite existing dashboards with matching UIDs."
printf "Continue? [y/N] "
read CONFIRM
if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
    echo "Aborted."
    exit 0
fi

# Restore dashboards
RESTORED=0
FAILED=0

for FILE in "$DASHBOARD_DIR"/*.json; do
    [ -f "$FILE" ] || continue

    # Extract the dashboard object and wrap in import payload
    DASHBOARD_JSON=$(python3 -c "
import sys, json
data = json.load(open('$FILE'))
dashboard = data.get('dashboard', data)
dashboard.pop('id', None)
payload = {'dashboard': dashboard, 'overwrite': True}
print(json.dumps(payload))
" 2>/dev/null || true)

    if [ -z "$DASHBOARD_JSON" ]; then
        echo "  FAILED: Could not parse $(basename "$FILE")"
        FAILED=$((FAILED + 1))
        continue
    fi

    HTTP_CODE=$(echo "$DASHBOARD_JSON" | curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$GRAFANA_URL/api/dashboards/db" \
        -H "$AUTH_HEADER" \
        -H "Content-Type: application/json" \
        -d @-)

    if [ "$HTTP_CODE" = "200" ]; then
        RESTORED=$((RESTORED + 1))
        echo "  OK: $(basename "$FILE")"
    else
        FAILED=$((FAILED + 1))
        echo "  FAILED (HTTP $HTTP_CODE): $(basename "$FILE")"
    fi
done

# Restore alert rules
if [ -f "$BACKUP_DIR/alert_rules.json" ]; then
    RULE_COUNT=$(python3 -c "import json; print(len(json.load(open('$BACKUP_DIR/alert_rules.json'))))" 2>/dev/null || echo "0")
    echo "  Alert rules in backup: $RULE_COUNT (manual import recommended via Grafana UI)"
fi

echo ""
echo "=== Grafana Restore Complete ==="
echo "  Restored: $RESTORED dashboards"
if [ $FAILED -gt 0 ]; then
    echo "  Failed: $FAILED dashboards"
    exit 1
fi
