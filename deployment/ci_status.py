#!/usr/bin/env python3
"""Exit 0 only if every GitHub check run on a commit completed green.

Stdlib only: the deploy runs this with the Pi's system python3.
"""

import json
import sys
import urllib.error
import urllib.request

REPO = "vvaten/redhouse"
TIMEOUT_S = 20


def check_runs_url(sha: str) -> str:
    return f"https://api.github.com/repos/{REPO}/commits/{sha}/check-runs?per_page=100"


def verdict(payload: dict) -> tuple:
    """(green, reason) for a check-runs API response.
    No runs is not green: CI may not have started yet."""
    runs = payload["check_runs"]
    if not runs:
        return False, "no check runs yet"
    if payload["total_count"] != len(runs):
        return False, "only {} of {} check runs read".format(len(runs), payload["total_count"])
    bad = [
        "{}: {}/{}".format(r["name"], r["status"], r["conclusion"])
        for r in runs
        if r["status"] != "completed" or r["conclusion"] != "success"
    ]
    if bad:
        return False, "not green: " + ", ".join(bad)
    return True, f"all {len(runs)} check runs passed"


def fetch(sha: str) -> dict:
    request = urllib.request.Request(
        check_runs_url(sha), headers={"Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return json.load(response)


def main(argv: list) -> int:
    if len(argv) != 2:
        print("Usage: ci_status.py <commit sha>")
        return 2
    sha = argv[1]
    try:
        green, reason = verdict(fetch(sha))
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        green, reason = False, f"could not read CI status: {e}"
    print(f"CI for {sha[:12]}: {reason}")
    return 0 if green else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
