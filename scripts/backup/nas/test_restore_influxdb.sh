#!/bin/sh
# Verify InfluxDB backup integrity without performing a full restore.
#
# Checks:
#   1. Backup directory exists and has files
#   2. Manifest is valid and reports success
#   3. Backup file count is reasonable (>100 files expected)
#   4. Backup size is reasonable (>50 MB expected)
#   5. Production buckets have recent data (confirms DB is healthy)
#
# A full restore test was performed manually on 2026-04-03 and confirmed
# 11520/11520 records for spotprice (100% match). Full restore takes ~10 min
# per bucket due to InfluxDB scanning all shards, so automated restore
# testing is not practical for regular use.
#
# Usage:
#   ./test_restore_influxdb.sh /share/Backups/redhouse/nas/latest
#   ./test_restore_influxdb.sh /share/Backups/redhouse/nas/2026-04-03_033000

# Ensure Asustor system binaries are in PATH
PATH="/usr/builtin/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export PATH

set -eu

BACKUP_BASE="$1"

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
MANIFEST="$BACKUP_BASE/backup_manifest.json"

PASSED=0
FAILED=0

pass() {
    echo "  PASS: $1"
    PASSED=$((PASSED + 1))
}

fail() {
    echo "  FAIL: $1"
    FAILED=$((FAILED + 1))
}

echo "=== InfluxDB Backup Verification ==="
echo "  Backup: $BACKUP_BASE"
echo ""

# Check 1: Backup directory exists
if [ -d "$BACKUP_DIR" ]; then
    pass "Backup directory exists"
else
    fail "Backup directory not found: $BACKUP_DIR"
    echo ""
    echo "=== FAILED ==="
    exit 1
fi

# Check 2: File count is reasonable
FILE_COUNT=$(ls -1 "$BACKUP_DIR" 2>/dev/null | wc -l)
if [ "$FILE_COUNT" -gt 100 ]; then
    pass "Backup has $FILE_COUNT files (expected >100)"
else
    fail "Backup has only $FILE_COUNT files (expected >100)"
fi

# Check 3: Backup size is reasonable
BACKUP_SIZE_KB=$(du -sk "$BACKUP_DIR" 2>/dev/null | cut -f1)
if [ "$BACKUP_SIZE_KB" -gt 51200 ]; then
    BACKUP_SIZE_MB=$((BACKUP_SIZE_KB / 1024))
    pass "Backup size is ${BACKUP_SIZE_MB} MB (expected >50 MB)"
else
    fail "Backup size is ${BACKUP_SIZE_KB} KB (expected >50 MB)"
fi

# Check 4: Manifest exists and reports success
if [ -f "$MANIFEST" ]; then
    INFLUX_STATUS=$(python3 -c "import json; print(json.load(open('$MANIFEST')).get('influxdb',{}).get('status','unknown'))" 2>/dev/null || echo "unknown")
    if [ "$INFLUX_STATUS" = "ok" ]; then
        pass "Manifest reports influxdb status: ok"
    else
        fail "Manifest reports influxdb status: $INFLUX_STATUS"
    fi
else
    fail "Manifest not found: $MANIFEST"
fi

# Check 5: Key production buckets have recent data
BUCKETS_TO_CHECK="spotprice weather emeters checkwatt_full_data"
for BUCKET in $BUCKETS_TO_CHECK; do
    COUNT=$(docker exec "$CONTAINER" influx query \
        --host "$INFLUX_HOST" --token "$OPERATOR_TOKEN" --org "$ORG" \
        --raw \
        "from(bucket: \"$BUCKET\") |> range(start: -2d) |> group() |> count()" \
        2>/dev/null | grep "^," | tail -1 | awk -F',' '{print $NF}' || echo "0")

    if [ -n "$COUNT" ] && [ "$COUNT" -gt 0 ]; then
        pass "Production bucket '$BUCKET' has $COUNT records (last 2d)"
    else
        fail "Production bucket '$BUCKET' has no recent data (last 2d)"
    fi
done

# Summary
echo ""
echo "=== Verification Results ==="
echo "  Passed: $PASSED"
echo "  Failed: $FAILED"

if [ $FAILED -gt 0 ]; then
    exit 1
fi
exit 0
