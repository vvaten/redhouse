#!/bin/sh
# Test InfluxDB backup restoration by restoring to temporary _restore_test buckets.
#
# Safe to run anytime -- never touches production buckets.
# Creates temporary buckets, restores backup into them, compares record counts
# against production, then cleans up.
#
# Requires:
#   - Docker access (run as root)
#   - Operator token
#   - /backups volume mounted in influxdb2 container
#
# Usage:
#   ./test_restore_influxdb.sh /share/Backups/redhouse/nas/latest
#   ./test_restore_influxdb.sh /share/Backups/redhouse/nas/2026-04-03_033000
#   ./test_restore_influxdb.sh /share/Backups/redhouse/nas/latest --full

# Ensure Asustor system binaries are in PATH
PATH="/usr/builtin/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export PATH

set -eu

BACKUP_BASE="$1"
MODE="${2:-}"

CONTAINER="influxdb2"
INFLUX_HOST="http://localhost:8086"
ORG="area51"

OPERATOR_TOKEN_FILE="/share/Backups/redhouse/nas/.operator_token"

if [ ! -f "$OPERATOR_TOKEN_FILE" ]; then
    echo "ERROR: Operator token file not found: $OPERATOR_TOKEN_FILE"
    exit 1
fi

OPERATOR_TOKEN=$(cat "$OPERATOR_TOKEN_FILE")
BACKUP_DIR="$BACKUP_BASE/influxdb"

if [ ! -d "$BACKUP_DIR" ]; then
    echo "ERROR: Backup influxdb directory not found: $BACKUP_DIR"
    exit 1
fi

# Test bucket to restore into
TEST_BUCKET="spotprice_restore_test"
PROD_BUCKET="spotprice"

if [ "$MODE" = "--full" ]; then
    TEST_BUCKETS="spotprice_restore_test temperatures_restore_test weather_restore_test"
    PROD_BUCKETS="spotprice temperatures weather"
else
    TEST_BUCKETS="$TEST_BUCKET"
    PROD_BUCKETS="$PROD_BUCKET"
fi

# Map host path to container path
SNAPSHOT_NAME=$(basename "$BACKUP_BASE")
CONTAINER_PATH="/backups/$SNAPSHOT_NAME/influxdb"

PASSED=0
FAILED=0
CLEANUP_BUCKETS=""

echo "=== InfluxDB Restore Test ==="
echo "  Backup: $BACKUP_DIR"
echo "  Mode: $(if [ "$MODE" = "--full" ]; then echo "full (multiple buckets)"; else echo "quick (spotprice only)"; fi)"
echo ""

# Cleanup function -- delete test buckets even on failure
cleanup() {
    echo ""
    echo "Cleaning up test buckets..."
    for BUCKET in $CLEANUP_BUCKETS; do
        # Get bucket ID
        BUCKET_ID=$(docker exec "$CONTAINER" influx bucket list \
            --host "$INFLUX_HOST" --token "$OPERATOR_TOKEN" \
            --org "$ORG" --name "$BUCKET" 2>/dev/null | \
            grep "$BUCKET" | awk '{print $1}')

        if [ -n "$BUCKET_ID" ]; then
            docker exec "$CONTAINER" influx bucket delete \
                --host "$INFLUX_HOST" --token "$OPERATOR_TOKEN" \
                --id "$BUCKET_ID" > /dev/null 2>&1 && \
                echo "  Deleted: $BUCKET" || \
                echo "  WARNING: Could not delete $BUCKET"
        fi
    done
}

# Set trap for cleanup on exit (normal or error)
trap cleanup EXIT

# For each test bucket pair
set -- $PROD_BUCKETS
for TEST_BKT in $TEST_BUCKETS; do
    PROD_BKT="$1"
    shift

    echo "Testing: $PROD_BKT -> $TEST_BKT"
    CLEANUP_BUCKETS="$CLEANUP_BUCKETS $TEST_BKT"

    # Step 1: Get production record count (last 30 days)
    PROD_COUNT=$(docker exec "$CONTAINER" influx query \
        --host "$INFLUX_HOST" --token "$OPERATOR_TOKEN" --org "$ORG" \
        --raw \
        "from(bucket: \"$PROD_BKT\") |> range(start: -30d) |> group() |> count()" \
        2>/dev/null | grep -v "^#" | grep -v "^$" | grep -v "^," | tail -1 | rev | cut -d',' -f1 | rev || echo "0")

    if [ -z "$PROD_COUNT" ] || [ "$PROD_COUNT" = "0" ]; then
        echo "  WARNING: No production data in $PROD_BKT (last 30d), skipping"
        continue
    fi
    echo "  Production record count (30d): $PROD_COUNT"

    # Step 2: Create temporary test bucket
    docker exec "$CONTAINER" influx bucket create \
        --host "$INFLUX_HOST" --token "$OPERATOR_TOKEN" \
        --org "$ORG" --name "$TEST_BKT" \
        --retention 604800 > /dev/null 2>&1

    echo "  Created test bucket: $TEST_BKT"

    # Step 3: Restore backup into test bucket
    echo "  Restoring backup..."
    docker exec "$CONTAINER" influx restore "$CONTAINER_PATH" \
        --host "$INFLUX_HOST" --token "$OPERATOR_TOKEN" \
        --bucket "$PROD_BKT" --new-bucket "$TEST_BKT" \
        2>&1 | grep -v "^$" | sed 's/^/    /' || true

    # Step 4: Count restored records
    RESTORED_COUNT=$(docker exec "$CONTAINER" influx query \
        --host "$INFLUX_HOST" --token "$OPERATOR_TOKEN" --org "$ORG" \
        --raw \
        "from(bucket: \"$TEST_BKT\") |> range(start: -30d) |> group() |> count()" \
        2>/dev/null | grep -v "^#" | grep -v "^$" | grep -v "^," | tail -1 | rev | cut -d',' -f1 | rev || echo "0")

    echo "  Restored record count (30d): $RESTORED_COUNT"

    # Step 5: Compare (allow 1% tolerance for timing)
    if [ -n "$RESTORED_COUNT" ] && [ "$RESTORED_COUNT" != "0" ]; then
        # Simple comparison -- restored should be >= 99% of production
        THRESHOLD=$((PROD_COUNT * 99 / 100))
        if [ "$RESTORED_COUNT" -ge "$THRESHOLD" ]; then
            echo "  PASS: $PROD_BKT ($RESTORED_COUNT >= $THRESHOLD)"
            PASSED=$((PASSED + 1))
        else
            echo "  FAIL: $PROD_BKT (restored $RESTORED_COUNT < threshold $THRESHOLD)"
            FAILED=$((FAILED + 1))
        fi
    else
        echo "  FAIL: $PROD_BKT (no data restored)"
        FAILED=$((FAILED + 1))
    fi
    echo ""
done

# Summary
echo "=== Test Results ==="
echo "  Passed: $PASSED"
echo "  Failed: $FAILED"

if [ $FAILED -gt 0 ]; then
    exit 1
fi
exit 0
