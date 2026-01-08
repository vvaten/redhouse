#!/usr/bin/env python3
"""
Create InfluxDB buckets for aggregation pipeline.

Creates buckets for production, test, and staging environments:
- emeters_5min: 90 days retention (5-minute aggregated energy meter data)
- analytics_15min: 5 years retention (15-minute analytics with joined data)
- analytics_1hour: 5 years retention (1-hour analytics with joined data)
"""

import sys
import os
import logging

# Add parent directory to path so we can import src module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from influxdb_client import InfluxDBClient, BucketRetentionRules
from src.common.config import get_config

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def create_bucket_if_not_exists(
    client: InfluxDBClient, org_id: str, bucket_name: str, retention_seconds: int
) -> bool:
    """
    Create an InfluxDB bucket if it doesn't already exist.

    Args:
        client: InfluxDB client
        org_id: Organization ID
        bucket_name: Name of bucket to create
        retention_seconds: Retention period in seconds (0 = infinite)

    Returns:
        True if created or already exists, False on error
    """
    buckets_api = client.buckets_api()

    try:
        # Check if bucket already exists
        existing = buckets_api.find_bucket_by_name(bucket_name)
        if existing:
            logger.info(f"Bucket '{bucket_name}' already exists")
            return True

        # Create bucket with retention policy
        retention_rules = BucketRetentionRules(type="expire", every_seconds=retention_seconds)

        bucket = buckets_api.create_bucket(
            bucket_name=bucket_name, org_id=org_id, retention_rules=retention_rules
        )

        if retention_seconds == 0:
            logger.info(f"Created bucket '{bucket_name}' with infinite retention")
        else:
            logger.info(
                f"Created bucket '{bucket_name}' with {retention_seconds / 86400:.0f} days retention"
            )
        return True

    except Exception as e:
        logger.error(f"Error creating bucket '{bucket_name}': {e}")
        return False


def main():
    """Create all aggregation buckets."""
    config = get_config()

    logger.info("Connecting to InfluxDB...")
    client = InfluxDBClient(
        url=config.influxdb_url, token=config.influxdb_token, org=config.influxdb_org
    )

    try:
        # Get organization ID
        orgs_api = client.organizations_api()
        orgs = orgs_api.find_organizations(org=config.influxdb_org)
        if not orgs:
            logger.error(f"Organization '{config.influxdb_org}' not found")
            return 1

        org_id = orgs[0].id
        logger.info(f"Using organization '{config.influxdb_org}' (ID: {org_id})")

        # Define buckets with retention policies
        # Retention: 90 days = 7776000 seconds, 5 years = 157680000 seconds, 0 = infinite
        buckets_to_create = [
            # Production buckets
            ("emeters_5min", 90 * 86400),  # 90 days
            ("analytics_15min", 5 * 365 * 86400),  # 5 years
            ("analytics_1hour", 0),  # Infinite - keep forever for historical analysis
            # Test buckets
            ("emeters_5min_test", 7 * 86400),  # 7 days for testing
            ("analytics_15min_test", 7 * 86400),  # 7 days for testing
            ("analytics_1hour_test", 7 * 86400),  # 7 days for testing
            # Staging buckets
            ("emeters_5min_staging", 30 * 86400),  # 30 days for staging
            ("analytics_15min_staging", 30 * 86400),  # 30 days for staging
            ("analytics_1hour_staging", 30 * 86400),  # 30 days for staging
        ]

        logger.info(f"\nCreating {len(buckets_to_create)} buckets...")
        success_count = 0
        fail_count = 0

        for bucket_name, retention_seconds in buckets_to_create:
            if create_bucket_if_not_exists(client, org_id, bucket_name, retention_seconds):
                success_count += 1
            else:
                fail_count += 1

        logger.info(f"\nBucket creation complete: {success_count} successful, {fail_count} failed")

        if fail_count > 0:
            return 1

        return 0

    except Exception as e:
        logger.error(f"Error: {e}")
        return 1

    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
