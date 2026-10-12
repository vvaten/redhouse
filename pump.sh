#!/bin/bash
# Manual heat pump control: redhouse-pump ON|ALE|EVU|status [--dry-run]
# Runs control_pump.py as pi, who owns the pump state file.
set -euo pipefail

DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
RUN=("$DIR/venv/bin/python" "$DIR/control_pump.py" "$@")

writes=$([ $# -gt 0 ] && echo true || echo false)
for arg in "$@"; do
    case "$arg" in status | --dry-run | -h | --help) writes=false ;; esac
done

if [ "$writes" = true ]; then
    # The executor and the boot restore write the same relays and state file,
    # and nothing locks them
    states=$(systemctl is-active redhouse-execute-program.service \
        redhouse-pump-restore.service || true)
    if grep -qx activating <<< "$states"; then
        echo "The executor or the boot restore is running: try again in a minute" >&2
        exit 1
    fi
    if [ $(( $(date +%-M) % 15 )) -eq 14 ]; then
        echo "The heating program executor starts within a minute: try again after it" >&2
        exit 1
    fi
fi

if [ "$(id -un)" = "pi" ]; then
    exec "${RUN[@]}"
fi
exec sudo -u pi "${RUN[@]}"
