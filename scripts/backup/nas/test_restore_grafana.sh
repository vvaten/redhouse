#!/bin/sh
# Test Grafana backup restoration by importing dashboards with [TEST] prefix.
#
# Safe to run anytime -- creates test copies of dashboards, verifies they
# import correctly, then deletes them.
#
# Requires:
#   - Grafana API key with admin permissions
#
# Usage:
#   ./test_restore_grafana.sh /share/Backups/redhouse/nas/latest
#   ./test_restore_grafana.sh /share/Backups/redhouse/nas/2026-04-03_033000

# Ensure Asustor system binaries are in PATH
PATH="/usr/builtin/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export PATH

set -eu

BACKUP_BASE="$1"

GRAFANA_URL="http://localhost:3000"
GRAFANA_API_KEY_FILE="/share/Backups/redhouse/nas/.grafana_api_key"

if [ ! -f "$GRAFANA_API_KEY_FILE" ]; then
    echo "ERROR: Grafana API key file not found: $GRAFANA_API_KEY_FILE"
    exit 1
fi

GRAFANA_API_KEY=$(cat "$GRAFANA_API_KEY_FILE")
AUTH_HEADER="Authorization: Bearer $GRAFANA_API_KEY"

DASHBOARD_DIR="$BACKUP_BASE/grafana/dashboards"

if [ ! -d "$DASHBOARD_DIR" ]; then
    echo "ERROR: Dashboard directory not found: $DASHBOARD_DIR"
    exit 1
fi

DASHBOARD_COUNT=$(ls -1 "$DASHBOARD_DIR"/*.json 2>/dev/null | wc -l)

echo "=== Grafana Restore Test ==="
echo "  Backup: $DASHBOARD_DIR"
echo "  Dashboards to test: $DASHBOARD_COUNT"
echo ""

PASSED=0
FAILED=0
TEST_UIDS=""

# Cleanup function -- delete test dashboards even on failure
cleanup() {
    echo ""
    echo "Cleaning up test dashboards..."
    for UID in $TEST_UIDS; do
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
            -X DELETE "$GRAFANA_URL/api/dashboards/uid/$UID" \
            -H "$AUTH_HEADER")
        if [ "$HTTP_CODE" = "200" ]; then
            echo "  Deleted: $UID"
        else
            echo "  WARNING: Could not delete $UID (HTTP $HTTP_CODE)"
        fi
    done
}

trap cleanup EXIT

for FILE in "$DASHBOARD_DIR"/*.json; do
    [ -f "$FILE" ] || continue
    FILENAME=$(basename "$FILE")

    # Create test import payload: modify UID and title to avoid collisions
    TEST_PAYLOAD=$(python3 -c "
import sys, json
data = json.load(open('$FILE'))
dashboard = data.get('dashboard', data)
dashboard.pop('id', None)
orig_uid = dashboard.get('uid', 'unknown')
dashboard['uid'] = 'test_' + orig_uid
dashboard['title'] = '[TEST] ' + dashboard.get('title', 'unknown')
payload = {'dashboard': dashboard, 'overwrite': True}
print(json.dumps(payload))
" 2>/dev/null || true)

    if [ -z "$TEST_PAYLOAD" ]; then
        echo "  FAIL: Could not parse $FILENAME"
        FAILED=$((FAILED + 1))
        continue
    fi

    # Extract test UID for cleanup
    TEST_UID=$(echo "$TEST_PAYLOAD" | python3 -c "import sys,json; print(json.load(sys.stdin)['dashboard']['uid'])" 2>/dev/null || echo "")
    if [ -n "$TEST_UID" ]; then
        TEST_UIDS="$TEST_UIDS $TEST_UID"
    fi

    # Import test dashboard
    HTTP_CODE=$(echo "$TEST_PAYLOAD" | curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$GRAFANA_URL/api/dashboards/db" \
        -H "$AUTH_HEADER" \
        -H "Content-Type: application/json" \
        -d @-)

    if [ "$HTTP_CODE" = "200" ]; then
        # Verify it exists by fetching it back
        VERIFY_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
            -H "$AUTH_HEADER" \
            "$GRAFANA_URL/api/dashboards/uid/$TEST_UID")

        if [ "$VERIFY_CODE" = "200" ]; then
            echo "  PASS: $FILENAME (imported + verified)"
            PASSED=$((PASSED + 1))
        else
            echo "  FAIL: $FILENAME (imported but verify failed, HTTP $VERIFY_CODE)"
            FAILED=$((FAILED + 1))
        fi
    else
        echo "  FAIL: $FILENAME (import HTTP $HTTP_CODE)"
        FAILED=$((FAILED + 1))
    fi
done

# Check alert rules JSON is valid
if [ -f "$BACKUP_BASE/grafana/alert_rules.json" ]; then
    if python3 -c "import json; json.load(open('$BACKUP_BASE/grafana/alert_rules.json'))" 2>/dev/null; then
        echo "  PASS: alert_rules.json (valid JSON)"
        PASSED=$((PASSED + 1))
    else
        echo "  FAIL: alert_rules.json (invalid JSON)"
        FAILED=$((FAILED + 1))
    fi
fi

# Summary
echo ""
echo "=== Test Results ==="
echo "  Passed: $PASSED"
echo "  Failed: $FAILED"

if [ $FAILED -gt 0 ]; then
    exit 1
fi
exit 0
