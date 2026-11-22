#!/bin/bash
# Manual hot water control: hotwater status | anchor "YYYY-MM-DD HH:MM" |
# circulate <min> | pause <min> | commission
# Runs hot_water_cli.py as pi, the user of the hot water timer.
set -euo pipefail

DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
RUN=("$DIR/venv/bin/python" "$DIR/hot_water_cli.py" "$@")

if [ "$(id -un)" = "pi" ]; then
    exec "${RUN[@]}"
fi
exec sudo -u pi "${RUN[@]}"
