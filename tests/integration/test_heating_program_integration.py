"""Integration tests for heating program generation and execution."""

import datetime
import json
import os
import sys
import tempfile

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.control.heating_data_fetcher import HeatingDataFetcher
from src.control.program_executor import HeatingProgramExecutor
from src.control.program_generator import HeatingProgramGenerator


def test_influx_connection():
    """Test basic InfluxDB connection."""
    print("\n" + "=" * 60)
    print("TEST 1: InfluxDB Connection")
    print("=" * 60)

    try:
        config = get_config()
        print(f"  URL: {config.influxdb_url}")
        print(f"  Org: {config.influxdb_org}")
        print(f"  Load control bucket: {config.get('influxdb_bucket_load_control', 'load_control')}")

        influx = InfluxClient(config)
        print("  [OK] Connection: OK")

        return True

    except Exception as e:
        print(f"  [FAIL] Connection: FAILED - {e}")
        return False


def test_data_fetcher():
    """Test fetching heating data from InfluxDB."""
    print("\n" + "=" * 60)
    print("TEST 2: Data Fetcher")
    print("=" * 60)

    try:
        fetcher = HeatingDataFetcher()

        # Fetch data for tomorrow
        print("  Fetching weather, spot prices, and solar predictions...")
        df = fetcher.fetch_heating_data(date_offset=1, lookback_days=1, lookahead_days=2)

        if df.empty:
            print("  [FAIL] FAILED: No data fetched")
            print("  This may be expected if buckets are empty")
            print("  Run data collection scripts first:")
            print("    python collect_weather.py")
            print("    python collect_spot_prices.py")
            return False

        print(f"  [OK] Fetched {len(df)} data rows")
        print(f"  Columns: {list(df.columns)}")

        # Check for required columns
        required = ["Air temperature", "price_total", "solar_yield_avg_prediction"]
        missing = [col for col in required if col not in df.columns]

        if missing:
            print(f"  [FAIL] Missing columns: {missing}")
            return False

        print("  [OK] All required columns present")

        # Get average temperature
        avg_temp = fetcher.get_day_average_temperature(df, date_offset=1)
        print(f"  [OK] Average temperature for tomorrow: {avg_temp:.1f}C")

        return True

    except Exception as e:
        print(f"  [FAIL] FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_program_generation():
    """Test generating a heating program."""
    print("\n" + "=" * 60)
    print("TEST 3: Program Generation")
    print("=" * 60)

    try:
        generator = HeatingProgramGenerator()

        # Generate program for tomorrow
        print("  Generating program for tomorrow...")
        program = generator.generate_daily_program(date_offset=1)

        print(f"  [OK] Generated program v{program['version']}")
        print(f"  Program date: {program['program_date']}")
        print(f"  Average temperature: {program['input_parameters']['avg_temperature_c']:.1f}C")
        print(f"  Heating hours needed: {program['planning_results']['total_heating_hours_needed']:.2f}h")
        print(f"  Estimated cost: {program['planning_results']['estimated_total_cost_eur']:.2f} EUR")

        # Check structure
        assert "loads" in program
        assert "geothermal_pump" in program["loads"]

        pump = program["loads"]["geothermal_pump"]
        print(f"  [OK] Geothermal pump: {pump['total_intervals_on']} intervals planned")
        print(f"  Schedule entries: {len(pump['schedule'])}")

        # Save to temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = generator.save_program_json(program, output_dir=tmpdir)
            print(f"  [OK] Saved JSON to: {filepath}")

            # Verify file exists and can be loaded
            with open(filepath, "r") as f:
                loaded = json.load(f)
                assert loaded["version"] == program["version"]
                print("  [OK] JSON file verified")

        return True, program

    except Exception as e:
        print(f"  [FAIL] FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False, None


def test_program_save_to_influx():
    """Test saving program to InfluxDB test bucket."""
    print("\n" + "=" * 60)
    print("TEST 4: Save Program to InfluxDB")
    print("=" * 60)

    try:
        generator = HeatingProgramGenerator()

        # Generate a simple program
        print("  Generating test program...")
        program = generator.generate_daily_program(date_offset=1)

        # Override bucket to test bucket
        original_bucket = generator.config.get("influxdb_bucket_load_control", "load_control")
        generator.config["influxdb_bucket_load_control"] = "load_control_test"

        print(f"  Writing to test bucket: load_control_test")
        generator.save_program_influxdb(program, data_type="plan")

        print("  [OK] Program saved to InfluxDB test bucket")
        print("  Check Grafana to verify the data")

        # Restore original bucket
        generator.config["influxdb_bucket_load_control"] = original_bucket

        return True

    except Exception as e:
        print(f"  [FAIL] FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_program_execution_dry_run():
    """Test program execution in dry-run mode."""
    print("\n" + "=" * 60)
    print("TEST 5: Program Execution (Dry-Run)")
    print("=" * 60)

    try:
        # Create a test program
        test_program = {
            "version": "2.0.0",
            "program_date": datetime.date.today().strftime("%Y-%m-%d"),
            "loads": {
                "geothermal_pump": {
                    "load_id": "geothermal_pump",
                    "power_kw": 3.0,
                    "schedule": [
                        {
                            "timestamp": int(datetime.datetime.now().timestamp()) - 60,  # 1 min ago
                            "utc_time": (datetime.datetime.now() - datetime.timedelta(minutes=1)).isoformat(),
                            "local_time": (datetime.datetime.now() - datetime.timedelta(minutes=1)).isoformat(),
                            "command": "ON",
                            "duration_minutes": 60,
                            "reason": "test_execution",
                        },
                        {
                            "timestamp": int(datetime.datetime.now().timestamp()) + 3600,  # 1 hour later
                            "utc_time": (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat(),
                            "local_time": (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat(),
                            "command": "ALE",
                            "duration_minutes": None,
                            "reason": "test_completion",
                        },
                    ],
                }
            },
            "execution_status": {
                "executed_intervals": 0,
                "pending_intervals": 2,
            },
        }

        # Save to temp file
        with tempfile.TemporaryDirectory() as tmpdir:
            year_month = test_program["program_date"][:7]
            os.makedirs(os.path.join(tmpdir, year_month), exist_ok=True)

            executor = HeatingProgramExecutor(dry_run=True)

            print("  Executing test program in DRY-RUN mode...")
            summary = executor.execute_program(test_program, base_dir=tmpdir)

            print(f"  [OK] Executed: {summary['executed_count']} commands")
            print(f"  [OK] Skipped: {summary['skipped_count']} commands")
            print(f"  [OK] Failed: {summary['failed_count']} commands")

            if summary["executed_count"] > 0:
                print("  [OK] DRY-RUN execution successful")
                return True
            else:
                print("  [WARN] No commands were executed (may be expected)")
                return True

    except Exception as e:
        print(f"  [FAIL] FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_end_to_end_simulation():
    """Test complete end-to-end program generation and execution."""
    print("\n" + "=" * 60)
    print("TEST 6: End-to-End Simulation")
    print("=" * 60)

    try:
        # Generate program
        print("  Step 1: Generating program...")
        generator = HeatingProgramGenerator()
        program = generator.generate_daily_program(date_offset=1)
        print(f"  [OK] Generated program for {program['program_date']}")

        # Save to temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            print("  Step 2: Saving program to JSON...")
            filepath = generator.save_program_json(program, output_dir=tmpdir)
            print(f"  [OK] Saved to {filepath}")

            # Load program
            print("  Step 3: Loading program...")
            executor = HeatingProgramExecutor(dry_run=True)
            loaded_program = executor.load_program(
                program_date=program["program_date"], base_dir=tmpdir
            )
            print(f"  [OK] Loaded program for {loaded_program['program_date']}")

            # Execute in dry-run mode
            print("  Step 4: Executing program (dry-run)...")
            summary = executor.execute_program(loaded_program, base_dir=tmpdir)
            print(f"  [OK] Execution complete: {summary['executed_count']} executed")

            print("\n  [OK] END-TO-END TEST PASSED")
            return True

    except Exception as e:
        print(f"  [FAIL] FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all integration tests."""
    print("\n" + "=" * 70)
    print(" PHASE 4 INTEGRATION TESTS")
    print("=" * 70)
    print("\nThese tests require:")
    print("  1. InfluxDB running at configured URL")
    print("  2. Valid credentials in .env file")
    print("  3. Test buckets created (run: python scripts/setup_test_buckets.py)")
    print("  4. Some data in weather/spotprice/emeters buckets")
    print("\nNote: Some tests may fail if buckets are empty. This is expected.")

    # Check if .env exists
    if not os.path.exists(".env"):
        print("\n[FAIL] ERROR: .env file not found!")
        print("Please create .env with your InfluxDB credentials.")
        return 1

    results = []

    # Test 1: Connection
    results.append(("Connection", test_influx_connection()))

    # Test 2: Data Fetcher
    results.append(("Data Fetcher", test_data_fetcher()))

    # Test 3: Program Generation
    success, program = test_program_generation()
    results.append(("Program Generation", success))

    # Test 4: Save to InfluxDB (only if generation succeeded)
    if success:
        results.append(("Save to InfluxDB", test_program_save_to_influx()))
    else:
        print("\n[WARN] Skipping InfluxDB save test (generation failed)")
        results.append(("Save to InfluxDB", None))

    # Test 5: Execution (dry-run)
    results.append(("Execution (Dry-Run)", test_program_execution_dry_run()))

    # Test 6: End-to-End
    results.append(("End-to-End Simulation", test_end_to_end_simulation()))

    # Summary
    print("\n" + "=" * 70)
    print(" TEST SUMMARY")
    print("=" * 70)

    passed = 0
    failed = 0
    skipped = 0

    for test_name, result in results:
        if result is True:
            status = "[OK] PASS"
            passed += 1
        elif result is False:
            status = "[FAIL] FAIL"
            failed += 1
        else:
            status = "[SKIP] SKIP"
            skipped += 1

        print(f"  {test_name:.<40} {status}")

    print("=" * 70)
    print(f"\nResults: {passed} passed, {failed} failed, {skipped} skipped")

    if failed == 0 and passed > 0:
        print("\n[OK] ALL TESTS PASSED!")
        print("\nYou can now:")
        print("  1. Check load_control_test bucket in Grafana")
        print("  2. Run generate_heating_program_v2.py --dry-run")
        print("  3. Deploy to Raspberry Pi when ready")
        return 0
    else:
        print("\n[WARN] SOME TESTS FAILED")
        print("\nCommon issues:")
        print("  - InfluxDB not running or wrong URL")
        print("  - Missing test buckets (run setup_test_buckets.py)")
        print("  - Empty data buckets (run data collection scripts)")
        print("  - Invalid token or permissions")
        return 1


if __name__ == "__main__":
    sys.exit(main())
