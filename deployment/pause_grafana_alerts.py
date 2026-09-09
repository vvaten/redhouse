#!/usr/bin/env python3
"""Pause or resume the Grafana data freshness alerts of one environment.

Pausing keeps each rule and its history and only stops evaluation, so no
notifications are sent. Use it when an environment is deliberately idle,
for example when the staging timers are stopped, otherwise every
freshness rule fires and mails the contact point.

Writes through the rule-group endpoint, the same one
setup_grafana_alerts.py uses, because the single-rule PUT rejects fields
that the GET returns.

Usage:
    python -u deployment/pause_grafana_alerts.py --env staging
    python -u deployment/pause_grafana_alerts.py --env staging --resume
    python -u deployment/pause_grafana_alerts.py --env staging --dry-run
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_GRAFANA_URL = "http://192.168.1.164:3000"
FOLDER_TITLE_PATTERN = "RedHouse-Alerts-{env}"
GROUP_NAME_PATTERN = "Data-Freshness-{env}"
REQUEST_TIMEOUT_SECONDS = 30


def grafana_api(base_url, path, api_key, method="GET", data=None):
    """Make a Grafana API request and return the decoded body."""
    body = json.dumps(data).encode() if data is not None else None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Disable-Provenance": "true",
    }
    request = urllib.request.Request(
        f"{base_url}/api{path}", data=body, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = response.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"HTTP {e.code} on {method} {path}: {detail}") from e
    return json.loads(payload) if payload else None


def find_folder_uid(base_url, api_key, folder_title):
    """Return the uid of the folder with this title, or None."""
    folders = grafana_api(base_url, "/folders", api_key)
    for folder in folders:
        if folder["title"] == folder_title:
            return folder["uid"]
    print(f"ERROR: no folder titled '{folder_title}'", file=sys.stderr)
    print(f"Available: {', '.join(sorted(f['title'] for f in folders))}", file=sys.stderr)
    return None


def parse_args(argv=None):
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Pause or resume Grafana data freshness alerts for one environment"
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=["wibatemp", "staging", "production"],
        help="Which environment's alert group to act on",
    )
    parser.add_argument(
        "--grafana-url",
        default=DEFAULT_GRAFANA_URL,
        help=f"Grafana base URL (default: {DEFAULT_GRAFANA_URL})",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("GRAFANA_API_KEY"),
        help="Grafana API key (or set GRAFANA_API_KEY env var)",
    )
    parser.add_argument("--resume", action="store_true", help="Unpause instead of pause")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would change, write nothing"
    )
    return parser.parse_args(argv)


def find_rule_group_path(base_url, api_key, env):
    """Return the provisioning path of one environment's rule group."""
    env_label = env.capitalize()
    folder_uid = find_folder_uid(base_url, api_key, FOLDER_TITLE_PATTERN.format(env=env_label))
    if folder_uid is None:
        return None
    group_name = GROUP_NAME_PATTERN.format(env=env_label)
    return f"/v1/provisioning/folder/{folder_uid}/rule-groups/{group_name}"


def select_rules_to_change(group, target_paused):
    """Print each rule's state and return those needing a change."""
    pending = [r for r in group["rules"] if bool(r.get("isPaused", False)) != target_paused]
    pending_titles = {r["title"] for r in pending}
    for rule in group["rules"]:
        state = "would change" if rule["title"] in pending_titles else "already set"
        print(f"  [{state}] {rule['title']}")
    return pending


def write_pause_state(base_url, api_key, path, group, target_paused):
    """Write isPaused for every rule in the group, then report the result."""
    for rule in group["rules"]:
        rule["isPaused"] = target_paused
    grafana_api(base_url, path, api_key, method="PUT", data=group)

    after = grafana_api(base_url, path, api_key)
    paused = sum(1 for r in after["rules"] if r.get("isPaused"))
    print(f"Wrote {path.rsplit('/', 1)[-1]}: {paused} of {len(after['rules'])} rules paused")


def apply_pause_state(base_url, api_key, env, target_paused, dry_run):
    """Set isPaused on every rule of one environment's group."""
    path = find_rule_group_path(base_url, api_key, env)
    if path is None:
        return 1

    group = grafana_api(base_url, path, api_key)
    verb = "pause" if target_paused else "resume"
    pending = select_rules_to_change(group, target_paused)
    total = len(group["rules"])

    if not pending:
        print(f"Nothing to do: all {total} rules already {verb}d")
        return 0

    if dry_run:
        print(f"[DRY-RUN] Would {verb} {len(pending)} of {total} rules")
        return 0

    write_pause_state(base_url, api_key, path, group, target_paused)
    return 0


def main():
    """Main entry point."""
    args = parse_args()

    if not args.api_key:
        print("ERROR: No API key. Set GRAFANA_API_KEY or use --api-key", file=sys.stderr)
        return 1

    try:
        return apply_pause_state(
            args.grafana_url.rstrip("/"),
            args.api_key,
            args.env,
            target_paused=not args.resume,
            dry_run=args.dry_run,
        )
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
