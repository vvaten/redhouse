"""Integration tests for the full aggregation pipeline.

Tests the complete flow:
1. Write 1-minute CheckWatt + Shelly data
2. Run 5-minute aggregator
3. Verify 5-minute data is written with correct measurement name
4. Run 15-minute aggregator
5. Verify 15-minute analytics data is written
6. Run 1-hour aggregator
7. Verify 1-hour analytics data is written
"""

import datetime

from src.aggregation.analytics_1hour import run_aggregation as run_1hour_aggregation
from src.aggregation.analytics_15min import run_aggregation as run_15min_aggregation
from src.aggregation.emeters_5min import aggregate_5min

from .conftest import write_checkwatt_data, write_shelly_data


def test_5min_aggregation_writes_energy_measurement(
    influx_client, config, test_timestamp, cleanup_test_data
):
    """Test that 5-minute aggregator writes data with measurement='energy'."""
    # Write 1-minute source data for a 5-minute window
    window_end = test_timestamp.replace(minute=5, second=0, microsecond=0)
    window_start = window_end - datetime.timedelta(minutes=5)

    for i in range(5):
        timestamp = window_start + datetime.timedelta(minutes=i)
        write_checkwatt_data(
            influx_client, config, timestamp, consumption=1000, solar=500, battery_discharge=200
        )
        write_shelly_data(
            influx_client,
            config,
            timestamp,
            phase_a=300,
            phase_b=300,
            phase_c=300,
            baseline_timestamp=window_start,
        )

    # Run 5-minute aggregator
    result = aggregate_5min(window_end, dry_run=False)
    assert result == 0, "5-minute aggregation failed"

    # Query the 5-minute bucket and verify measurement name
    # Note: range stop is exclusive, so add 1 second to include data AT window_end
    stop_time = window_end + datetime.timedelta(seconds=1)
    query = f"""
from(bucket: "{config.influxdb_bucket_emeters_5min}")
  |> range(start: {window_start.isoformat()}, stop: {stop_time.isoformat()})
  |> filter(fn: (r) => r._measurement == "energy")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
    tables = influx_client.query_api.query(query, org=config.influxdb_org)

    data = []
    for table in tables:
        for record in table.records:
            data.append(record.values)

    assert len(data) == 1, f"Expected 1 data point, got {len(data)}"

    # Verify key fields exist
    record = data[0]
    assert "solar_yield_avg" in record
    assert "consumption_avg" in record
    assert "solar_yield_diff" in record
    assert "consumption_diff" in record


def test_15min_aggregation_reads_from_5min_bucket(
    influx_client, config, test_timestamp, cleanup_test_data
):
    """Test that 15-minute aggregator reads from 5-minute bucket and writes analytics."""
    # Create 3 x 5-minute windows of data
    window_end = test_timestamp.replace(minute=15, second=0, microsecond=0)

    baseline_time = window_end - datetime.timedelta(minutes=15)
    for window in range(3):
        window_5min_end = (
            window_end
            - datetime.timedelta(minutes=15)
            + datetime.timedelta(minutes=(window + 1) * 5)
        )
        window_5min_start = window_5min_end - datetime.timedelta(minutes=5)

        # Write 1-minute source data
        for i in range(5):
            timestamp = window_5min_start + datetime.timedelta(minutes=i)
            write_checkwatt_data(
                influx_client, config, timestamp, consumption=1000, solar=600, battery_discharge=100
            )
            write_shelly_data(
                influx_client,
                config,
                timestamp,
                phase_a=300,
                phase_b=300,
                phase_c=300,
                baseline_timestamp=baseline_time,
            )

        # Run 5-minute aggregator
        result = aggregate_5min(window_5min_end, dry_run=False)
        assert result == 0, f"5-minute aggregation failed for window {window}"

    # Run 15-minute aggregator
    success = run_15min_aggregation(window_end, dry_run=False)
    assert success, "15-minute aggregation failed"

    # Query analytics_15min bucket
    query_start = window_end - datetime.timedelta(minutes=1)
    query_stop = window_end + datetime.timedelta(minutes=1)
    query = f"""
from(bucket: "{config.influxdb_bucket_analytics_15min}")
  |> range(start: {query_start.isoformat()}, stop: {query_stop.isoformat()})
  |> filter(fn: (r) => r._measurement == "analytics")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
    tables = influx_client.query_api.query(query, org=config.influxdb_org)

    data = []
    for table in tables:
        for record in table.records:
            data.append(record.values)

    assert len(data) == 1, f"Expected 1 analytics data point, got {len(data)}"

    # Verify analytics fields
    record = data[0]
    assert "consumption_avg" in record
    assert "consumption_sum" in record
    assert "solar_yield_avg" in record
    assert "solar_yield_sum" in record
    assert "self_consumption_ratio" in record

    # Verify values are reasonable
    assert record["consumption_sum"] > 0
    assert record["solar_yield_sum"] > 0


def test_1hour_aggregation_reads_from_5min_bucket(
    influx_client, config, test_timestamp, cleanup_test_data
):
    """Test that 1-hour aggregator reads from 5-minute bucket and writes analytics."""
    # Create 12 x 5-minute windows of data (1 hour)
    window_end = test_timestamp.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(
        hours=1
    )

    baseline_time = window_end - datetime.timedelta(hours=1)
    for window in range(12):
        window_5min_end = (
            window_end
            - datetime.timedelta(minutes=60)
            + datetime.timedelta(minutes=(window + 1) * 5)
        )
        window_5min_start = window_5min_end - datetime.timedelta(minutes=5)

        # Write 1-minute source data with varying values
        for i in range(5):
            timestamp = window_5min_start + datetime.timedelta(minutes=i)
            consumption = 1000 + (window * 50)  # Vary consumption
            solar = 500 + (window * 30)  # Vary solar
            write_checkwatt_data(
                influx_client,
                config,
                timestamp,
                consumption=consumption,
                solar=solar,
                battery_discharge=150,
            )
            write_shelly_data(
                influx_client,
                config,
                timestamp,
                phase_a=300,
                phase_b=300,
                phase_c=300,
                baseline_timestamp=baseline_time,
            )

        # Run 5-minute aggregator
        result = aggregate_5min(window_5min_end, dry_run=False)
        assert result == 0, f"5-minute aggregation failed for window {window}"

    # Run 1-hour aggregator
    success = run_1hour_aggregation(window_end, dry_run=False)
    assert success, "1-hour aggregation failed"

    # Query analytics_1hour bucket
    query_start = window_end - datetime.timedelta(minutes=1)
    query_stop = window_end + datetime.timedelta(minutes=1)
    query = f"""
from(bucket: "{config.influxdb_bucket_analytics_1hour}")
  |> range(start: {query_start.isoformat()}, stop: {query_stop.isoformat()})
  |> filter(fn: (r) => r._measurement == "analytics")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
    tables = influx_client.query_api.query(query, org=config.influxdb_org)

    data = []
    for table in tables:
        for record in table.records:
            data.append(record.values)

    assert len(data) == 1, f"Expected 1 analytics data point, got {len(data)}"

    # Verify analytics fields including peak values
    record = data[0]
    assert "consumption_avg" in record
    assert "consumption_sum" in record
    assert "consumption_max" in record  # 1-hour has max values
    assert "solar_yield_avg" in record
    assert "solar_yield_sum" in record
    assert "solar_yield_max" in record
    assert "self_consumption_ratio" in record

    # Verify values are reasonable
    assert record["consumption_sum"] > 0
    assert record["solar_yield_sum"] > 0
    assert record["consumption_max"] >= record["consumption_avg"]
    assert record["solar_yield_max"] >= record["solar_yield_avg"]


def test_analytics_cost_calculation(influx_client, config, test_timestamp, cleanup_test_data):
    """Test that cost calculations are performed correctly in analytics."""
    # Write spot price data
    from influxdb_client import Point

    window_end = test_timestamp.replace(minute=15, second=0, microsecond=0)

    # Write spot price for the hour (spot prices are hourly, at hour boundaries)
    # Note: measurement name is "spot", not "spotprice"
    hour_start = window_end.replace(minute=0, second=0, microsecond=0)
    price_point = (
        Point("spot")
        .time(hour_start)
        .field("price_total", 10.0)  # 10 cents/kWh
        .field("price_sell", 5.0)  # 5 cents/kWh
    )
    influx_client.write_api.write(
        bucket=config.influxdb_bucket_spotprice,
        org=config.influxdb_org,
        record=price_point,
    )

    # Create 3 x 5-minute windows with high solar export
    baseline_time = window_end - datetime.timedelta(minutes=15)
    for window in range(3):
        window_5min_end = (
            window_end
            - datetime.timedelta(minutes=15)
            + datetime.timedelta(minutes=(window + 1) * 5)
        )
        window_5min_start = window_5min_end - datetime.timedelta(minutes=5)

        # High solar, low consumption to enable export
        # Grid power = consumption - solar = 200 - 1000 = -800W (exporting)
        for i in range(5):
            timestamp = window_5min_start + datetime.timedelta(minutes=i)
            write_checkwatt_data(
                influx_client, config, timestamp, consumption=200, solar=1000, battery_discharge=0
            )
            # Grid exports 800W, split across 3 phases
            write_shelly_data(
                influx_client,
                config,
                timestamp,
                phase_a=-267,
                phase_b=-267,
                phase_c=-266,
                baseline_timestamp=baseline_time,
            )

        result = aggregate_5min(window_5min_end, dry_run=False)
        assert result == 0

    # Run 15-minute aggregator
    success = run_15min_aggregation(window_end, dry_run=False)
    assert success

    # Query and verify cost fields exist
    query_start = window_end - datetime.timedelta(minutes=1)
    query_stop = window_end + datetime.timedelta(minutes=1)
    query = f"""
from(bucket: "{config.influxdb_bucket_analytics_15min}")
  |> range(start: {query_start.isoformat()}, stop: {query_stop.isoformat()})
  |> filter(fn: (r) => r._measurement == "analytics")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
    tables = influx_client.query_api.query(query, org=config.influxdb_org)

    data = []
    for table in tables:
        for record in table.records:
            data.append(record.values)

    assert len(data) == 1
    record = data[0]

    # Debug: print all fields to see what we got
    print("\nAnalytics record fields:")
    for key, value in sorted(record.items()):
        if not key.startswith("_"):
            print(f"  {key}: {value}")

    # Verify cost fields exist
    assert "solar_direct_value" in record
    assert "solar_export_revenue" in record
    assert "grid_import_cost" in record

    # Verify solar yield sum is reasonable for high solar scenario
    assert record["solar_yield_sum"] > 0

    # With high solar (1000W) and low consumption (200W), we should have export revenue
    # Solar = 1000W * 5min * 3 windows / 60 = 250 Wh
    # Consumption = 200W * 5min * 3 windows / 60 = 50 Wh
    # Export = 250 - 50 = 200 Wh = 0.2 kWh
    # Revenue = 0.2 kWh * 5 cents/kWh = 1 cent
    assert (
        record.get("solar_export_revenue", 0) > 0
    ), f"Expected solar export revenue > 0, got {record.get('solar_export_revenue')}"
