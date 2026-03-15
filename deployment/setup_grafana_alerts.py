#!/usr/bin/env python3
"""Set up Grafana data freshness alert rules via the Grafana API.

Creates alert rules that monitor InfluxDB buckets for stale data.
If no new data arrives within the configured threshold, the alert fires
and notifies via the configured contact point.

Usage:
    python deployment/setup_grafana_alerts.py --env staging [--dry-run]
    python deployment/setup_grafana_alerts.py --env production [--dry-run]
    python deployment/setup_grafana_alerts.py --env wibatemp [--dry-run]
    python deployment/setup_grafana_alerts.py --env all [--dry-run]

Environments:
    wibatemp    - Current production (wibatemp writes to production buckets)
    staging     - RedHouse staging (writes to *_staging buckets)
    production  - RedHouse production (when it takes over production buckets)

Requires:
    GRAFANA_API_KEY environment variable (or --api-key flag)
    GRAFANA_CONTACT_POINT_NAME environment variable (or --contact-point flag)
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# Data freshness alert definitions:
# Each entry defines a production bucket, measurement, and max allowed staleness.
# Staging bucket names are derived by appending "_staging" suffix.
ALERT_RULES = [
    {
        "name": "Temperature data stale",
        "bucket": "temperatures",
        "measurement": "temperatures",
        "max_age_minutes": 10,
        "eval_interval_seconds": 300,
        "skip_envs": ["staging"],
    },
    {
        "name": "Energy meter data stale",
        "bucket": "shelly_em3_emeters_raw",
        "measurement": "shelly_em3",
        "wibatemp_bucket": "emeters",
        "wibatemp_measurement": "energy",
        "wibatemp_max_age_minutes": 30,
        "max_age_minutes": 5,
        "eval_interval_seconds": 120,
    },
    {
        "name": "CheckWatt data stale",
        "bucket": "checkwatt_full_data",
        "measurement": "checkwatt",
        "max_age_minutes": 120,
        "eval_interval_seconds": 600,
    },
    {
        "name": "Weather data stale",
        "bucket": "weather",
        "measurement": "weather",
        "max_age_minutes": 720,
        "eval_interval_seconds": 1800,
    },
    {
        "name": "Wind power data stale",
        "bucket": "windpower",
        "measurement": "windpower",
        "max_age_minutes": 480,
        "eval_interval_seconds": 1800,
    },
    {
        "name": "5min aggregation stale",
        "bucket": "emeters_5min",
        "measurement": "energy",
        "max_age_minutes": 15,
        "eval_interval_seconds": 300,
        "skip_envs": ["wibatemp"],
    },
    {
        "name": "15min aggregation stale",
        "bucket": "analytics_15min",
        "measurement": "analytics",
        "max_age_minutes": 45,
        "eval_interval_seconds": 600,
        "skip_envs": ["wibatemp"],
    },
    {
        "name": "1hour aggregation stale",
        "bucket": "analytics_1hour",
        "measurement": "analytics",
        "max_age_minutes": 180,
        "eval_interval_seconds": 1800,
        "skip_envs": ["wibatemp"],
    },
]

# Bucket name mapping from production to staging
STAGING_BUCKET_MAP = {
    "temperatures": "temperatures_staging",
    "shelly_em3_emeters_raw": "shelly_em3_emeters_raw_staging",
    "checkwatt_full_data": "checkwatt_staging",
    "weather": "weather_staging",
    "windpower": "windpower_staging",
    "emeters_5min": "emeters_5min_staging",
    "analytics_15min": "analytics_15min_staging",
    "analytics_1hour": "analytics_1hour_staging",
}

CONTACT_POINT_NAME_ENV = "GRAFANA_CONTACT_POINT_NAME"


def grafana_api(base_url, path, api_key, method="GET", data=None):
    """Make a Grafana API request."""
    url = f"{base_url}/api{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_body = resp.read().decode("utf-8")
            if resp_body:
                return json.loads(resp_body)
            return None
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        print(f"API error {e.code} for {method} {path}: {error_body}", file=sys.stderr)
        raise


def find_or_create_folder(base_url, api_key, title):
    """Find existing folder or create a new one."""
    folders = grafana_api(base_url, "/folders", api_key)
    for folder in folders:
        if folder["title"] == title:
            print(f"Found existing folder: {title} (uid={folder['uid']})")
            return folder["uid"]

    folder = grafana_api(
        base_url,
        "/folders",
        api_key,
        method="POST",
        data={"title": title},
    )
    print(f"Created folder: {title} (uid={folder['uid']})")
    return folder["uid"]


def find_datasource_uid(base_url, api_key):
    """Find the InfluxDB datasource UID."""
    datasources = grafana_api(base_url, "/datasources", api_key)
    for ds in datasources:
        if ds["type"] == "influxdb":
            print(f"Found InfluxDB datasource: {ds['name']} (uid={ds['uid']})")
            return ds["uid"]
    raise RuntimeError("No InfluxDB datasource found in Grafana")


def build_flux_query(bucket, measurement, max_age_minutes):
    """Build a Flux query that returns seconds since last data point."""
    return (
        f'import "system"\n'
        f"\n"
        f'from(bucket: "{bucket}")\n'
        f"  |> range(start: -{max_age_minutes * 2}m)\n"
        f'  |> filter(fn: (r) => r["_measurement"] == "{measurement}")\n'
        f"  |> last()\n"
        f"  |> map(fn: (r) => ({{_time: r._time, _value: "
        f"float(v: uint(v: system.time()) - uint(v: r._time)) / 1000000000.0}}))\n"
        f"  |> group()\n"
        f"  |> min()\n"
        f'  |> keep(columns: ["_value"])\n'
    )


def build_threshold_query(max_age_seconds):
    """Build a Grafana threshold expression query data entry."""
    return {
        "refId": "threshold",
        "queryType": "",
        "relativeTimeRange": {"from": 0, "to": 0},
        "datasourceUid": "-100",
        "model": {
            "refId": "threshold",
            "type": "threshold",
            "expression": "A",
            "datasource": {"type": "__expr__", "uid": "-100"},
            "conditions": [
                {
                    "evaluator": {"type": "gt", "params": [max_age_seconds]},
                    "operator": {"type": "and"},
                    "query": {"params": ["A"]},
                    "reducer": {"params": [], "type": "last"},
                    "type": "query",
                }
            ],
            "hide": False,
            "intervalMs": 1000,
            "maxDataPoints": 43200,
        },
    }


def build_alert_rule(rule_def, datasource_uid, folder_uid, env_name, group_name):
    """Build a Grafana alert rule from a rule definition."""
    max_age_minutes = rule_def["max_age_minutes"]
    bucket = rule_def["bucket"]
    measurement = rule_def["measurement"]

    if env_name == "wibatemp":
        bucket = rule_def.get("wibatemp_bucket", bucket)
        measurement = rule_def.get("wibatemp_measurement", measurement)
        max_age_minutes = rule_def.get("wibatemp_max_age_minutes", max_age_minutes)
    elif env_name == "staging":
        bucket = STAGING_BUCKET_MAP[bucket]

    max_age_seconds = max_age_minutes * 60
    flux_query = build_flux_query(bucket, measurement, max_age_minutes)

    env_labels = {"wibatemp": "[Wibatemp] ", "staging": "[Staging] ", "production": ""}
    env_prefix = env_labels[env_name]

    flux_data = {
        "refId": "A",
        "queryType": "",
        "relativeTimeRange": {"from": max_age_minutes * 2 * 60, "to": 0},
        "datasourceUid": datasource_uid,
        "model": {
            "refId": "A",
            "query": flux_query,
            "intervalMs": 1000,
            "maxDataPoints": 43200,
            "hide": False,
        },
    }

    return {
        "title": f"{env_prefix}{rule_def['name']}",
        "ruleGroup": group_name,
        "folderUID": folder_uid,
        "orgId": 1,
        "condition": "threshold",
        "noDataState": "Alerting",
        "execErrState": "Alerting",
        "for": f"{rule_def['eval_interval_seconds']}s",
        "data": [flux_data, build_threshold_query(max_age_seconds)],
        "labels": {"source": "redhouse", "type": "data_freshness", "env": env_name},
        "annotations": {
            "summary": (
                f"{env_prefix}{rule_def['name']}: no new data in "
                f"{bucket}/{measurement} "
                f"for over {max_age_minutes} minutes"
            ),
        },
    }


def get_existing_rules(base_url, api_key, folder_uid):
    """Get existing alert rules in the folder."""
    rules = grafana_api(base_url, "/v1/provisioning/alert-rules", api_key)
    return [r for r in rules if r.get("folderUID") == folder_uid]


def setup_notification_policy(base_url, api_key, contact_point_name):
    """Set up notification policy to route RedHouse alerts to the email contact point."""
    try:
        policy = grafana_api(base_url, "/v1/provisioning/policies", api_key)
    except urllib.error.HTTPError:
        policy = {"receiver": "grafana-default-email"}

    # Check if we already have a child policy for redhouse
    children = policy.get("routes", [])
    for child in children:
        matchers = child.get("object_matchers", [])
        for matcher in matchers:
            if matcher == ["source", "=", "redhouse"]:
                print("Notification policy for redhouse already exists")
                return

    # Add a child policy
    children.append(
        {
            "receiver": contact_point_name,
            "object_matchers": [["source", "=", "redhouse"]],
            "group_wait": "30s",
            "group_interval": "5m",
            "repeat_interval": "1h",
        }
    )
    policy["routes"] = children

    grafana_api(
        base_url,
        "/v1/provisioning/policies",
        api_key,
        method="PUT",
        data=policy,
    )
    print(f"Added notification policy: source=redhouse -> {contact_point_name}")


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Set up Grafana data freshness alerts")
    parser.add_argument(
        "--grafana-url",
        default="http://192.168.1.164:3000",
        help="Grafana base URL (default: http://192.168.1.164:3000)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("GRAFANA_API_KEY"),
        help="Grafana API key (or set GRAFANA_API_KEY env var)",
    )
    parser.add_argument(
        "--contact-point",
        default=os.getenv(CONTACT_POINT_NAME_ENV),
        help=f"Grafana contact point name (or set {CONTACT_POINT_NAME_ENV} env var)",
    )
    parser.add_argument(
        "--env",
        choices=["wibatemp", "staging", "production", "all"],
        default="all",
        help="Which environment to create alerts for (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print alert rules without creating them",
    )
    return parser.parse_args()


def setup_environment_alerts(base_url, api_key, env_name, datasource_uid):
    """Create alert rules for a single environment."""
    folder_title = f"RedHouse-Alerts-{env_name.capitalize()}"
    group_name = f"Data-Freshness-{env_name.capitalize()}"

    folder_uid = find_or_create_folder(base_url, api_key, folder_title)

    # Delete existing rules in this folder to avoid duplicates
    existing = get_existing_rules(base_url, api_key, folder_uid)
    for rule in existing:
        print(f"Deleting existing rule: {rule['title']}")
        grafana_api(
            base_url,
            f"/v1/provisioning/alert-rules/{rule['uid']}",
            api_key,
            method="DELETE",
        )

    # Build rule group with all alert rules
    rule_group = {"title": group_name, "folderUid": folder_uid, "interval": 300, "rules": []}

    for rule_def in ALERT_RULES:
        if env_name in rule_def.get("skip_envs", []):
            print(f"  Skipped: {rule_def['name']} (not applicable for {env_name})")
            continue
        alert_rule = build_alert_rule(rule_def, datasource_uid, folder_uid, env_name, group_name)
        rule_group["rules"].append(alert_rule)
        print(f"  Prepared: {alert_rule['title']} " f"(max_age={rule_def['max_age_minutes']}min)")

    grafana_api(
        base_url,
        f"/v1/provisioning/folder/{folder_uid}/rule-groups/{group_name}",
        api_key,
        method="PUT",
        data=rule_group,
    )
    print(f"  Created {len(rule_group['rules'])} alert rules in '{group_name}'")


def main():
    """Set up Grafana data freshness alerts."""
    args = parse_args()

    if not args.api_key:
        print("ERROR: No API key. Set GRAFANA_API_KEY or use --api-key", file=sys.stderr)
        return 1

    if not args.contact_point:
        print(
            f"ERROR: No contact point. Set {CONTACT_POINT_NAME_ENV} or use --contact-point",
            file=sys.stderr,
        )
        return 1

    base_url = args.grafana_url.rstrip("/")
    if args.env == "all":
        environments = ["wibatemp", "staging", "production"]
    else:
        environments = [args.env]

    if args.dry_run:
        for env_name in environments:
            print(f"\n{env_name.upper()} - would create these alert rules:")
            for rule_def in ALERT_RULES:
                if env_name in rule_def.get("skip_envs", []):
                    print(f"  - {rule_def['name']}: SKIPPED (not applicable)")
                    continue
                bucket = rule_def["bucket"]
                measurement = rule_def["measurement"]
                max_age = rule_def["max_age_minutes"]
                if env_name == "wibatemp":
                    bucket = rule_def.get("wibatemp_bucket", bucket)
                    measurement = rule_def.get("wibatemp_measurement", measurement)
                    max_age = rule_def.get("wibatemp_max_age_minutes", max_age)
                elif env_name == "staging":
                    bucket = STAGING_BUCKET_MAP[bucket]
                print(
                    f"  - {rule_def['name']}: bucket={bucket}/{measurement},"
                    f" max_age={max_age}min"
                )
        return 0

    datasource_uid = find_datasource_uid(base_url, args.api_key)

    for env_name in environments:
        print(f"\n--- Setting up {env_name.upper()} alerts ---")
        setup_environment_alerts(base_url, args.api_key, env_name, datasource_uid)

    setup_notification_policy(base_url, args.api_key, args.contact_point)
    print("\nDone! Check Grafana -> Alerting -> Alert rules to verify.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
