#!/bin/sh
# Back up Grafana dashboards and alert rules via REST API.
#
# Exports all dashboards as JSON files and alert configuration.
# Runs on the NAS where Grafana is localhost.
#
# Usage:
#   ./backup_grafana.sh /share/Backups/redhouse/nas/2026-04-03_033000/grafana
#   ./backup_grafana.sh /share/Backups/redhouse/nas/2026-04-03_033000/grafana --dry-run

set -eu

BACKUP_DIR="$1"
DRY_RUN="${2:-}"

GRAFANA_URL="http://localhost:3000"
GRAFANA_API_KEY_FILE="/share/Backups/redhouse/nas/.grafana_api_key"

if [ ! -f "$GRAFANA_API_KEY_FILE" ]; then
    echo "ERROR: Grafana API key file not found: $GRAFANA_API_KEY_FILE"
    echo "Create it with: echo 'your-api-key' > $GRAFANA_API_KEY_FILE && chmod 600 $GRAFANA_API_KEY_FILE"
    exit 1
fi

GRAFANA_API_KEY=$(cat "$GRAFANA_API_KEY_FILE")

if [ -z "$GRAFANA_API_KEY" ]; then
    echo "ERROR: Grafana API key file is empty"
    exit 1
fi

AUTH_HEADER="Authorization: Bearer $GRAFANA_API_KEY"

echo "  Grafana backup -> $BACKUP_DIR"

if [ "$DRY_RUN" = "--dry-run" ]; then
    # Test API connectivity
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "$AUTH_HEADER" "$GRAFANA_URL/api/health")
    echo "  DRY-RUN: Grafana API health check: HTTP $HTTP_CODE"
    exit 0
fi

mkdir -p "$BACKUP_DIR/dashboards"

# Export dashboards
DASHBOARD_COUNT=0
DASHBOARD_UIDS=$(curl -s -H "$AUTH_HEADER" "$GRAFANA_URL/api/search?type=dash-db&limit=1000" | \
    python3 -c "import sys,json; [print(d['uid']) for d in json.load(sys.stdin)]" 2>/dev/null || true)

if [ -z "$DASHBOARD_UIDS" ]; then
    echo "  WARNING: No dashboards found or API error"
else
    for UID in $DASHBOARD_UIDS; do
        RESPONSE=$(curl -s -H "$AUTH_HEADER" "$GRAFANA_URL/api/dashboards/uid/$UID")

        # Extract title for filename
        TITLE=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('dashboard',{}).get('title','unknown'))" 2>/dev/null || echo "unknown")
        SAFE_TITLE=$(echo "$TITLE" | tr ' /:' '___' | tr -cd 'a-zA-Z0-9_-')

        echo "$RESPONSE" > "$BACKUP_DIR/dashboards/${UID}_${SAFE_TITLE}.json"
        DASHBOARD_COUNT=$((DASHBOARD_COUNT + 1))
    done
fi

# Export alert rules
curl -s -H "$AUTH_HEADER" "$GRAFANA_URL/api/v1/provisioning/alert-rules" \
    > "$BACKUP_DIR/alert_rules.json" 2>/dev/null || echo "[]" > "$BACKUP_DIR/alert_rules.json"

# Export contact points
curl -s -H "$AUTH_HEADER" "$GRAFANA_URL/api/v1/provisioning/contact-points" \
    > "$BACKUP_DIR/contact_points.json" 2>/dev/null || echo "[]" > "$BACKUP_DIR/contact_points.json"

# Export notification policies
curl -s -H "$AUTH_HEADER" "$GRAFANA_URL/api/v1/provisioning/policies" \
    > "$BACKUP_DIR/notification_policies.json" 2>/dev/null || echo "{}" > "$BACKUP_DIR/notification_policies.json"

BACKUP_SIZE=$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)
echo "  Grafana backup complete: $DASHBOARD_COUNT dashboards, $BACKUP_SIZE"
