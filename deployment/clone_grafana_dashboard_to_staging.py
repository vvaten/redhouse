#!/usr/bin/env python3
"""
Clone a Grafana dashboard and update all queries to use staging buckets.

This creates a copy of your production dashboard that reads from *_staging
buckets instead of production buckets, allowing side-by-side comparison.

Usage:
    python clone_grafana_dashboard_to_staging.py --dashboard-uid ABC123
    python clone_grafana_dashboard_to_staging.py --dashboard-uid ABC123 --dry-run

Requirements:
    pip install requests

Environment variables (add to .env):
    GRAFANA_URL=http://192.168.1.164:3000
    GRAFANA_API_KEY=your-api-key-here
"""

import argparse
import json
import os
import re
import sys
from typing import Any, Dict

import requests


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Clone Grafana dashboard to use staging buckets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--dashboard-uid",
        type=str,
        required=True,
        help="UID of the dashboard to clone (find in dashboard URL)",
    )

    parser.add_argument(
        "--grafana-url",
        type=str,
        default=os.getenv("GRAFANA_URL", "http://192.168.1.164:3000"),
        help="Grafana URL (default: from GRAFANA_URL env var)",
    )

    parser.add_argument(
        "--api-key",
        type=str,
        default=os.getenv("GRAFANA_API_KEY"),
        help="Grafana API key (default: from GRAFANA_API_KEY env var)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without creating the dashboard",
    )

    parser.add_argument(
        "--output",
        type=str,
        help="Save modified dashboard JSON to file (for inspection)",
    )

    return parser.parse_args()


def get_dashboard(grafana_url: str, api_key: str, dashboard_uid: str) -> Dict[str, Any]:
    """
    Fetch dashboard from Grafana.

    Args:
        grafana_url: Grafana base URL
        api_key: Grafana API key
        dashboard_uid: Dashboard UID

    Returns:
        Dashboard JSON
    """
    url = f"{grafana_url}/api/dashboards/uid/{dashboard_uid}"
    headers = {"Authorization": f"Bearer {api_key}"}

    print(f"Fetching dashboard: {dashboard_uid}")
    response = requests.get(url, headers=headers)
    response.raise_for_status()

    return response.json()


def replace_bucket_names(query: str) -> tuple[str, int]:
    """
    Replace production bucket names with staging bucket names in a query.

    Args:
        query: InfluxDB query string

    Returns:
        Tuple of (modified_query, replacement_count)
    """
    # Bucket name mappings
    replacements = {
        "temperatures": "temperatures_staging",
        "weather": "weather_staging",
        "spotprice": "spotprice_staging",
        "emeters": "emeters_staging",
        "checkwatt_full_data": "checkwatt_staging",
        "load_control": "load_control_staging",
    }

    modified = query
    count = 0

    # Match bucket names in from() clauses: from(bucket: "name")
    for prod_bucket, staging_bucket in replacements.items():
        # Pattern: from(bucket: "production_name")
        pattern = rf'from\(bucket:\s*["\']({re.escape(prod_bucket)})["\']'
        matches = re.findall(pattern, modified)
        if matches:
            modified = re.sub(pattern, f'from(bucket: "{staging_bucket}"', modified)
            count += len(matches)

    return modified, count


def update_dashboard_for_staging(dashboard_data: Dict[str, Any]) -> tuple[Dict[str, Any], int]:
    """
    Update dashboard to use staging buckets.

    Args:
        dashboard_data: Dashboard JSON

    Returns:
        Tuple of (modified_dashboard, total_replacements)
    """
    dashboard = dashboard_data["dashboard"]
    total_replacements = 0

    # Update title
    original_title = dashboard.get("title", "Unknown")
    dashboard["title"] = f"{original_title} (STAGING)"

    # Remove UID so Grafana creates a new dashboard
    if "uid" in dashboard:
        del dashboard["uid"]

    # Remove ID so Grafana creates a new dashboard
    if "id" in dashboard:
        del dashboard["id"]

    # Update all panels
    def process_panels(panels):
        nonlocal total_replacements
        for panel in panels:
            # Handle nested panels (rows)
            if "panels" in panel:
                process_panels(panel["panels"])

            # Update panel queries (targets)
            if "targets" in panel:
                for target in panel["targets"]:
                    if "query" in target:
                        modified_query, count = replace_bucket_names(target["query"])
                        target["query"] = modified_query
                        total_replacements += count

    if "panels" in dashboard:
        process_panels(dashboard["panels"])

    return {"dashboard": dashboard, "overwrite": False}, total_replacements


def create_dashboard(grafana_url: str, api_key: str, dashboard_data: Dict[str, Any]) -> str:
    """
    Create new dashboard in Grafana.

    Args:
        grafana_url: Grafana base URL
        api_key: Grafana API key
        dashboard_data: Dashboard JSON

    Returns:
        New dashboard UID
    """
    url = f"{grafana_url}/api/dashboards/db"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    print("Creating new dashboard...")
    response = requests.post(url, headers=headers, json=dashboard_data)
    response.raise_for_status()

    result = response.json()
    return result["uid"]


def main():
    """Main entry point."""
    args = parse_args()

    # Validate API key
    if not args.api_key:
        print("ERROR: Grafana API key required", file=sys.stderr)
        print("", file=sys.stderr)
        print("Set via environment variable or command line:", file=sys.stderr)
        print("  export GRAFANA_API_KEY=your-api-key-here", file=sys.stderr)
        print("  python clone_grafana_dashboard_to_staging.py --api-key YOUR_KEY", file=sys.stderr)
        print("", file=sys.stderr)
        print("To create an API key:", file=sys.stderr)
        print("  1. Open Grafana: http://192.168.1.164:3000", file=sys.stderr)
        print("  2. Go to: Configuration -> API Keys", file=sys.stderr)
        print("  3. Click 'Add API key'", file=sys.stderr)
        print("  4. Role: Editor", file=sys.stderr)
        return 1

    print("=" * 60)
    print("Cloning Grafana Dashboard to Staging")
    print("=" * 60)

    try:
        # Fetch original dashboard
        dashboard_data = get_dashboard(args.grafana_url, args.api_key, args.dashboard_uid)
        original_title = dashboard_data["dashboard"]["title"]
        print(f"Original: {original_title}")

        # Update for staging
        modified_data, replacements = update_dashboard_for_staging(dashboard_data)
        new_title = modified_data["dashboard"]["title"]
        print(f"New title: {new_title}")
        print(f"Bucket replacements: {replacements}")

        # Save to file if requested
        if args.output:
            with open(args.output, "w") as f:
                json.dump(modified_data, f, indent=2)
            print(f"Saved to: {args.output}")

        # Create dashboard (unless dry-run)
        if args.dry_run:
            print("\nDRY-RUN: Dashboard would be created with above changes")
            print("\nExample query change:")
            print('  from(bucket: "temperatures")')
            print('  -> from(bucket: "temperatures_staging")')
        else:
            new_uid = create_dashboard(args.grafana_url, args.api_key, modified_data)
            dashboard_url = f"{args.grafana_url}/d/{new_uid}"
            print("\n" + "=" * 60)
            print("SUCCESS! Staging dashboard created")
            print("=" * 60)
            print(f"URL: {dashboard_url}")
            print(f"UID: {new_uid}")
            print("\nYou can now view production and staging dashboards side-by-side!")

        return 0

    except requests.exceptions.HTTPError as e:
        print(f"\nERROR: HTTP {e.response.status_code}", file=sys.stderr)
        print(f"Response: {e.response.text}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
