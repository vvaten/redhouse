# Integration Tests

Integration tests for the RedHouse aggregation pipeline that connect to actual InfluxDB.

## Important Notes

- **Only uses `*_test` buckets** - completely isolated from production and staging
- **Run during development only** - not executed during deployment
- **Requires InfluxDB connection** - must have access to InfluxDB server
- **Cleans up after each test** - removes test data automatically

## Running Integration Tests

From the project root directory:

```bash
# Run all integration tests
pytest tests/integration/ -v

# Run specific test
pytest tests/integration/test_aggregation_pipeline.py::test_5min_aggregation_writes_energy_measurement -v

# Run with detailed output
pytest tests/integration/ -vv -s
```

## Test Coverage

### test_aggregation_pipeline.py

1. **test_5min_aggregation_writes_energy_measurement**
   - Writes 1-minute CheckWatt and Shelly data
   - Runs 5-minute aggregator
   - Verifies data is written with measurement="energy" (not "emeters_5min")
   - Verifies key fields exist

2. **test_15min_aggregation_reads_from_5min_bucket**
   - Creates 3x 5-minute windows
   - Runs 5-minute aggregator for each window
   - Runs 15-minute aggregator
   - Verifies analytics data is written correctly
   - Verifies field values are reasonable

3. **test_1hour_aggregation_reads_from_5min_bucket**
   - Creates 12x 5-minute windows (1 hour)
   - Runs 5-minute aggregator for each window
   - Runs 1-hour aggregator
   - Verifies analytics data including peak values
   - Verifies max values >= avg values

4. **test_analytics_cost_calculation**
   - Writes spot price data
   - Creates scenario with high solar export
   - Verifies cost calculation fields exist
   - Verifies export revenue is calculated

## Buckets Used

All tests use `*_test` buckets created by `deployment/create_aggregation_buckets.py`:

- `checkwatt_test` - Source CheckWatt data
- `shelly_em3_emeters_raw_test` - Source Shelly EM3 data
- `emeters_5min_test` - 5-minute aggregated data
- `analytics_15min_test` - 15-minute analytics
- `analytics_1hour_test` - 1-hour analytics
- `spotprice_test` - Spot price data for cost calculations

## What These Tests Catch

These integration tests would have caught the following bugs:

1. **Measurement name mismatch** - 5-minute aggregator writes "energy" but analytics aggregators queried "emeters_5min"
2. **WriteApi usage error** - Using `client.write_api()` instead of `client.write_api`
3. **Field mapping issues** - Missing or incorrectly named fields
4. **Data pipeline breakage** - Any break in the chain from source data to final analytics

## Requirements

- InfluxDB server running and accessible
- Test buckets created (run `deployment/create_aggregation_buckets.py`)
- Valid InfluxDB credentials in environment or .env file
