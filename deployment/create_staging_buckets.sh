#!/bin/bash
# Create InfluxDB staging buckets for RedHouse
# Run this on the InfluxDB server or from a machine with influx CLI configured

set -e

ORGANIZATION="area51"
RETENTION="0"  # Infinite retention (or set to e.g. "30d" for 30 days)

echo "Creating RedHouse staging buckets..."
echo "Organization: $ORGANIZATION"
echo "Retention: $RETENTION (0 = infinite)"
echo ""

BUCKETS=(
    "temperatures_staging"
    "weather_staging"
    "spotprice_staging"
    "emeters_staging"
    "checkwatt_staging"
    "load_control_staging"
)

for bucket in "${BUCKETS[@]}"; do
    echo "Creating bucket: $bucket"
    influx bucket create \
        -n "$bucket" \
        -o "$ORGANIZATION" \
        -r "$RETENTION" \
        2>&1 | grep -v "already exists" || true
done

echo ""
echo "Done! Verifying buckets..."
echo ""
influx bucket list -o "$ORGANIZATION" | grep "_staging"

echo ""
echo "All staging buckets created successfully!"
