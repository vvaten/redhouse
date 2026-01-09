#!/usr/bin/env python
"""Validate .env configuration file for completeness and safety."""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

# Required configuration keys that must be present
REQUIRED_KEYS = {
    "influxdb_connection": {
        "INFLUXDB_URL": "InfluxDB server URL",
        "INFLUXDB_TOKEN": "InfluxDB authentication token",
        "INFLUXDB_ORG": "InfluxDB organization name",
    },
    "influxdb_buckets": {
        "INFLUXDB_BUCKET_TEMPERATURES": "Temperature sensor data bucket",
        "INFLUXDB_BUCKET_WEATHER": "Weather forecast data bucket",
        "INFLUXDB_BUCKET_SPOTPRICE": "Electricity spot price bucket",
        "INFLUXDB_BUCKET_EMETERS": "Energy meter data bucket",
        "INFLUXDB_BUCKET_CHECKWATT": "CheckWatt API data bucket",
        "INFLUXDB_BUCKET_SHELLY_EM3_RAW": "Shelly EM3 raw data bucket",
        "INFLUXDB_BUCKET_LOAD_CONTROL": "Load control commands bucket (CRITICAL)",
        "INFLUXDB_BUCKET_EMETERS_5MIN": "5-minute aggregated energy data bucket",
        "INFLUXDB_BUCKET_ANALYTICS_15MIN": "15-minute analytics bucket",
        "INFLUXDB_BUCKET_ANALYTICS_1HOUR": "1-hour analytics bucket",
        "INFLUXDB_BUCKET_WINDPOWER": "Wind power data bucket",
    },
}

# Optional but recommended keys
RECOMMENDED_KEYS = {
    "STAGING_MODE": "Set to 'true' for staging deployment",
    "LOG_LEVEL": "Logging level (INFO, DEBUG, WARNING, ERROR)",
    "LOG_DIR": "Directory for log files",
}

# Keys that should not have placeholder values in production
PLACEHOLDER_PATTERNS = [
    "your-token-here",
    "your-password-here",
    "your-api-key-here",
    "your-email@example.com",
]


def check_placeholder_value(key: str, value: str) -> bool:
    """Check if value looks like a placeholder."""
    value_lower = value.lower()
    for pattern in PLACEHOLDER_PATTERNS:
        if pattern.lower() in value_lower:
            return True
    return False


def validate_env_file(env_path: str = ".env") -> tuple[bool, list[str], list[str]]:
    """
    Validate .env file for required configuration.

    Args:
        env_path: Path to .env file

    Returns:
        Tuple of (is_valid, errors, warnings)
    """
    errors = []
    warnings = []

    # Check if file exists
    if not os.path.exists(env_path):
        errors.append(f"File '{env_path}' does not exist!")
        return False, errors, warnings

    # Load environment variables
    load_dotenv(env_path)

    # Check required keys
    for category, keys in REQUIRED_KEYS.items():
        print(f"\nChecking {category}...")

        for key, description in keys.items():
            value = os.getenv(key)

            if not value:
                errors.append(f"MISSING: {key} - {description}")
                print(f"  [ERROR] {key}: MISSING")
            elif check_placeholder_value(key, value):
                warnings.append(f"PLACEHOLDER: {key} has placeholder value '{value}'")
                print(f"  [WARNING] {key}: Has placeholder value")
            else:
                print(f"  [OK] {key}")

    # Check recommended keys
    print("\nChecking optional/recommended settings...")
    for key, description in RECOMMENDED_KEYS.items():
        value = os.getenv(key)
        if not value:
            warnings.append(f"RECOMMENDED: {key} - {description}")
            print(f"  [INFO] {key}: Not set (recommended)")
        else:
            print(f"  [OK] {key}: {value}")

    # Check for staging mode safety
    staging_mode = os.getenv("STAGING_MODE", "false").lower() in ("true", "1", "yes")
    if staging_mode:
        print("\nSTAGING MODE ENABLED")
        print("  Checking bucket names have '_staging' suffix...")

        staging_issues = []
        for key in REQUIRED_KEYS["influxdb_buckets"].keys():
            if "LOAD_CONTROL" in key or key == "INFLUXDB_BUCKET_WEATHER":
                bucket = os.getenv(key, "")
                if bucket and not bucket.endswith("_staging"):
                    staging_issues.append(
                        f"  {key}={bucket} does not have '_staging' suffix"
                    )

        if staging_issues:
            warnings.append("STAGING MODE: Some buckets don't have '_staging' suffix:")
            warnings.extend(staging_issues)
            for issue in staging_issues:
                print(f"  [WARNING] {issue}")
        else:
            print("  [OK] All critical buckets have '_staging' suffix")

    is_valid = len(errors) == 0

    return is_valid, errors, warnings


def main():
    """Main entry point."""
    print("\n=== RedHouse .env Configuration Validator ===\n")

    # Check if .env file is specified
    env_path = sys.argv[1] if len(sys.argv) > 1 else ".env"

    print(f"Validating: {env_path}")

    is_valid, errors, warnings = validate_env_file(env_path)

    # Print summary
    print("\n=== Validation Summary ===\n")

    if errors:
        print(f"ERRORS ({len(errors)}):")
        for error in errors:
            print(f"  - {error}")
        print()

    if warnings:
        print(f"WARNINGS ({len(warnings)}):")
        for warning in warnings:
            print(f"  - {warning}")
        print()

    if is_valid:
        if warnings:
            print(
                "Configuration is VALID but has warnings. "
                "Review them before deployment."
            )
            sys.exit(0)
        else:
            print("Configuration is VALID and complete!")
            sys.exit(0)
    else:
        print(
            "Configuration is INVALID. "
            "Fix errors before running the system."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
