#!/usr/bin/env python3
"""Deploy a Grafana dashboard JSON file via the Grafana HTTP API.

Usage:
    python deployment/deploy_grafana_dashboard.py production
    python deployment/deploy_grafana_dashboard.py staging
    python deployment/deploy_grafana_dashboard.py path/to/dashboard.json
"""

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).parent.parent
DASHBOARD_DIR = PROJECT_DIR / "grafana" / "dashboards"

TARGETS = {
    "production": DASHBOARD_DIR / "production_dashboard.json",
    "staging": DASHBOARD_DIR / "staging_dashboard.json",
}


def main():
    load_dotenv()

    if len(sys.argv) < 2:
        print("Usage: python deploy_grafana_dashboard.py {production|staging|path/to.json}")
        sys.exit(1)

    target = sys.argv[1]
    if target in TARGETS:
        dashboard_file = TARGETS[target]
    else:
        dashboard_file = Path(target)

    if not dashboard_file.exists():
        print(f"ERROR: Dashboard file not found: {dashboard_file}", file=sys.stderr)
        sys.exit(1)

    grafana_url = os.getenv("GRAFANA_URL")
    api_key = os.getenv("GRAFANA_API_KEY")

    if not grafana_url:
        print("ERROR: GRAFANA_URL not set in .env", file=sys.stderr)
        sys.exit(1)
    if not api_key:
        print("ERROR: GRAFANA_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    grafana_url = grafana_url.rstrip("/")

    with open(dashboard_file) as f:
        dashboard = json.load(f)

    if "title" not in dashboard:
        print("ERROR: Dashboard JSON has no 'title' field", file=sys.stderr)
        sys.exit(1)

    # Null out instance-specific id (uid is the stable identifier)
    dashboard.pop("id", None)

    print(f"Deploying: {dashboard_file.name}")
    print(f"Title:     {dashboard['title']}")
    print(f"Grafana:   {grafana_url}")

    url = grafana_url + "/api/dashboards/db"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"dashboard": dashboard, "overwrite": True}

    r = requests.post(url, headers=headers, json=payload, timeout=30)

    if r.ok:
        result = r.json()
        print(f"[OK] Deployed: {grafana_url}{result.get('url', '')}")
    else:
        print(f"[FAIL] HTTP {r.status_code}: {r.text}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
