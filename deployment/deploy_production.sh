#!/bin/bash
# RedHouse Production Deployment Script
#
# Deploys latest main branch to /opt/redhouse with smart deploy timing.
# Assumes code has already been tested in staging.
#
# Only timers that are already running get restarted. During the gradual
# migration the rest are deliberately stopped because wibatemp still owns
# those jobs, so starting them all would put two systems on one job.
#
# Usage:
#   sudo deployment/deploy_production.sh              # Wait for safe window
#   sudo deployment/deploy_production.sh --now        # Deploy immediately
#   sudo deployment/deploy_production.sh --start-all  # Start every timer
#   sudo deployment/deploy_production.sh --skip-ci-check  # Deploy a red CI

set -e

# Resolved before any cd, so the copy on origin/main can be compared.
SELF=$(readlink -f "${BASH_SOURCE[0]}")
if [ -n "${REDHOUSE_DEPLOY_REEXEC:-}" ]; then
    rm -f "$REDHOUSE_DEPLOY_REEXEC"
fi

DEPLOY_DIR=/opt/redhouse
VENV_DIR="$DEPLOY_DIR/venv"
REPO_URL=git@github.com:vvaten/redhouse.git

# Smart deploy window configuration
# IMPORTANT: Keep in sync with systemd timer schedules in deployment/systemd/*.timer
OPTIMAL_WINDOWS=(7 22 37 52)
WINDOW_START_OFFSET=10

echo "=================================================="
echo "RedHouse Production Deployment"
echo "=================================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)"
    exit 1
fi

# The timers on local time fire by the host zone
HOST_TIMEZONE=$(timedatectl show -p Timezone --value)
if [ "$HOST_TIMEZONE" != "Europe/Helsinki" ]; then
    echo "ERROR: Host timezone is '$HOST_TIMEZONE', expected Europe/Helsinki"
    exit 1
fi

# --- Smart deploy window functions ---

in_optimal_window() {
    local current_minute
    current_minute=$(date +%M | sed 's/^0*//')
    local current_second
    current_second=$(date +%S | sed 's/^0*//')
    [ -z "$current_minute" ] && current_minute=0
    [ -z "$current_second" ] && current_second=0

    for window_start in "${OPTIMAL_WINDOWS[@]}"; do
        local window_end=$((window_start + 2))
        if [ "$current_minute" -eq "$window_start" ] && [ "$current_second" -ge "$WINDOW_START_OFFSET" ]; then
            return 0
        fi
        if [ "$current_minute" -gt "$window_start" ] && [ "$current_minute" -lt "$window_end" ]; then
            return 0
        fi
        if [ "$current_minute" -eq "$window_end" ] && [ "$current_second" -le 30 ]; then
            return 0
        fi
    done
    return 1
}

get_next_window() {
    local current_minute
    current_minute=$(date +%M | sed 's/^0*//')
    [ -z "$current_minute" ] && current_minute=0
    for window_start in "${OPTIMAL_WINDOWS[@]}"; do
        if [ "$current_minute" -lt "$window_start" ]; then
            echo "$window_start"
            return
        fi
    done
    echo "${OPTIMAL_WINDOWS[0]}"
}

wait_for_window() {
    if in_optimal_window; then
        echo "[OK] Currently in safe deployment window"
        return
    fi

    local next_window
    next_window=$(get_next_window)
    local current_minute
    current_minute=$(date +%M | sed 's/^0*//')
    local current_second
    current_second=$(date +%S | sed 's/^0*//')
    [ -z "$current_minute" ] && current_minute=0
    [ -z "$current_second" ] && current_second=0

    local minutes_to_wait
    if [ "$current_minute" -lt "$next_window" ]; then
        minutes_to_wait=$((next_window - current_minute))
    else
        minutes_to_wait=$((60 - current_minute + next_window))
    fi
    local wait_seconds=$((minutes_to_wait * 60 - current_second + WINDOW_START_OFFSET))

    echo "Waiting for safe deployment window (${wait_seconds}s)..."
    echo "Safe windows: :07, :22, :37, :52"
    echo "Press Ctrl+C to cancel"
    echo ""

    local remaining=$wait_seconds
    while [ $remaining -gt 0 ]; do
        printf "\rTime remaining: %02d:%02d " $((remaining / 60)) $((remaining % 60))
        sleep 1
        remaining=$((remaining - 1))
    done
    echo ""
    echo "[OK] Safe deployment window reached"
}

# --- Deploy gates ---

# Exit 0 only if every check run on that commit passed. The checker comes
# from HEAD, the installed commit, not from the commit judged. An
# unreadable or empty checker refuses the deploy.
ci_gate() {
    local checker
    if ! checker=$(sudo -u pi git show "HEAD:deployment/ci_status.py") || [ -z "$checker" ]; then
        echo "[FAIL] Cannot read deployment/ci_status.py at HEAD"
        return 1
    fi
    python3 -c "$checker" "$1"
}

# Exit 0 only if each timer is enabled/active or disabled/inactive, as
# should_run chose for it.
verify_timer_states() {
    local timer want got failed=0
    for timer in "${TIMERS[@]}"; do
        if should_run "$timer"; then
            want="enabled/active"
        else
            want="disabled/inactive"
        fi
        got="$(systemctl is-enabled "$timer.timer" 2>/dev/null || true)"
        got="$got/$(systemctl is-active "$timer.timer" 2>/dev/null || true)"
        if [ "$got" != "$want" ]; then
            echo "  [FAIL] $timer.timer is $got, expected $want"
            failed=1
        fi
    done
    return "$failed"
}

# Exit 0 only if every boot-only unit is enabled exactly when the
# executor timer runs: a boot restore drives the pump like the executor.
verify_boot_units() {
    local unit want got failed=0
    if should_run "redhouse-execute-program"; then want="enabled"; else want="disabled"; fi
    for unit in "${BOOT_UNITS[@]}"; do
        got="$(systemctl is-enabled "$unit.service" 2>/dev/null || true)"
        if [ "$got" != "$want" ]; then
            echo "  [FAIL] $unit.service is $got, expected $want"
            failed=1
        fi
    done
    return "$failed"
}

# A fresh install has no earlier commit to go back to.
rollback_hint() {
    if [ "$PRE_DEPLOY_COMMIT" != "(fresh install)" ]; then
        echo "Rollback: sudo -u pi git reset --hard ${PRE_DEPLOY_COMMIT}"
    fi
}

# Epoch seconds of a systemctl timestamp, or empty when it has none.
epoch_of() {
    case "$1" in
        "" | n/a | infinity) echo "" ;;
        *) date -d "$1" +%s 2>/dev/null || echo "" ;;
    esac
}

# These run their job on every timer restart by design.
FIRES_ON_RESTART=(
    "redhouse-temperature"
)

# Exit 0 only if no service started after the timers were stopped and
# before its timer was due, as recorded in DUE_AT. A Persistent catch-up
# was due, so it passes. A timer with no recorded due time only warns.
verify_no_offschedule_runs() {
    local timer raw started due failed=0
    for timer in "${TIMERS[@]}"; do
        case " ${FIRES_ON_RESTART[*]} " in
            *" $timer "*) continue ;;
        esac
        raw=$(systemctl show -p ExecMainStartTimestamp --value "$timer.service" 2>/dev/null || true)
        started=$(epoch_of "$raw")
        if [ -z "$started" ] && [ -n "$raw" ] && [ "$raw" != "n/a" ]; then
            echo "  [WARN] cannot read when $timer.service last started: '$raw'"
            continue
        fi
        if [ -z "$started" ] || [ "$started" -le "$QUIESCED_AT" ]; then
            continue
        fi
        due="${DUE_AT[$timer.timer]:-}"
        if [ -z "$due" ]; then
            echo "  [WARN] $timer.service ran during the deploy; it was not running before"
        elif [ "$started" -lt "$due" ]; then
            echo "  [FAIL] $timer.service ran during the deploy, before it was due"
            failed=1
        fi
    done
    return "$failed"
}

# --- Parse arguments ---

DEPLOY_NOW=false
START_ALL=false
SKIP_CI_CHECK=false
for arg in "$@"; do
    case "$arg" in
        --now)
            DEPLOY_NOW=true
            ;;
        --start-all)
            START_ALL=true
            ;;
        --skip-ci-check)
            SKIP_CI_CHECK=true
            ;;
        *)
            echo "ERROR: unknown option '$arg'"
            echo "Usage: $0 [--now] [--start-all] [--skip-ci-check]"
            exit 1
            ;;
    esac
done

# --- Stop the running timers for the duration ---
# The safe window cannot protect redhouse-temperature, which fires every
# minute, and pip can outlast the window anyway. A short known gap beats
# a run that imports half-updated code. Restored on any exit.
# Space separated, not newline: was_running matches on surrounding spaces.
RUNNING_TIMERS=$(systemctl list-units --state=active --plain --no-legend \
    'redhouse-*.timer' 2>/dev/null | awk '{print $1}' | grep -v staging | tr '\n' ' ' || true)

restore_timers() {
    if [ -z "$RUNNING_TIMERS" ]; then
        return
    fi
    echo ""
    echo "Restarting the timers that were running before the deploy..."
    for timer in $RUNNING_TIMERS; do
        if ! systemctl is-active -q "$timer"; then
            systemctl start "$timer" && echo "  [OK] $timer"
        fi
    done
}
trap restore_timers EXIT

# --- Initial setup or update ---

if [ -d "$DEPLOY_DIR/.git" ]; then
    echo "[UPDATE] Updating existing production installation..."
    cd "$DEPLOY_DIR"

    # Record pre-deploy commit for rollback
    PRE_DEPLOY_COMMIT=$(sudo -u pi git rev-parse HEAD)
    echo "Pre-deploy commit: $PRE_DEPLOY_COMMIT"

    # Stash local changes if any
    STASH_OUTPUT=$(sudo -u pi git stash 2>&1)
    if echo "$STASH_OUTPUT" | grep -q "Saved working directory"; then
        echo "[WARN] Local changes stashed: $STASH_OUTPUT"
        echo "       Recover with: git stash pop"
    fi

    if [ -z "${REDHOUSE_DEPLOY_SHA:-}" ]; then
        sudo -u pi git fetch origin main
        echo "[OK] Fetched latest from origin/main"
    fi
else
    echo "[INSTALL] Fresh production installation..."
    mkdir -p "$DEPLOY_DIR"
    chown pi:pi "$DEPLOY_DIR"
    sudo -u pi git clone "$REPO_URL" "$DEPLOY_DIR"
    cd "$DEPLOY_DIR"
    PRE_DEPLOY_COMMIT="(fresh install)"
    echo "[OK] Repository cloned"
fi

# --- Pin the commit this deploy gates, runs and merges ---
# A re-run copy gets it from the copy that started it, so a push in
# between cannot change what is deployed.

DEPLOY_SHA="${REDHOUSE_DEPLOY_SHA:-$(sudo -u pi git rev-parse origin/main)}"
echo "Deploying commit: $DEPLOY_SHA"

# --- Refuse a commit whose CI is not green ---

if [ "$SKIP_CI_CHECK" = true ]; then
    echo "[WARN] =================================================="
    echo "[WARN] CI CHECK SKIPPED (--skip-ci-check) for $DEPLOY_SHA"
    echo "[WARN] =================================================="
elif ! ci_gate "$DEPLOY_SHA"; then
    echo "[FAIL] Refusing to deploy $DEPLOY_SHA: CI is not green"
    echo "Wait for CI, or override with --skip-ci-check"
    exit 1
fi

# --- Run this script as it is in the pinned commit ---
# Bash keeps running the file it started with, so without this a fix
# to this script would only take effect from the deploy after next.

if [ "$PRE_DEPLOY_COMMIT" != "(fresh install)" ] && [ -z "${REDHOUSE_DEPLOY_REEXEC:-}" ]; then
    FRESH_SCRIPT=$(mktemp)
    if ! sudo -u pi git show "$DEPLOY_SHA:deployment/deploy_production.sh" > "$FRESH_SCRIPT"; then
        rm -f "$FRESH_SCRIPT"
        echo "[FAIL] Cannot read deploy_production.sh at $DEPLOY_SHA"
        exit 1
    fi
    if ! cmp -s "$FRESH_SCRIPT" "$SELF"; then
        echo "[INFO] deploy_production.sh differs in $DEPLOY_SHA, running that copy"
        trap - EXIT
        REDHOUSE_DEPLOY_REEXEC="$FRESH_SCRIPT" REDHOUSE_DEPLOY_SHA="$DEPLOY_SHA" \
            exec bash "$FRESH_SCRIPT" "$@"
    fi
    rm -f "$FRESH_SCRIPT"
fi

# --- Wait for safe window (or skip with --now) ---

if [ "$DEPLOY_NOW" = true ]; then
    echo "[WARN] Deploying immediately (--now flag, skipping window check)"
else
    wait_for_window
fi

# --- Quiesce the running timers before the files change ---

declare -A DUE_AT
for timer in $RUNNING_TIMERS; do
    DUE_AT[$timer]=$(epoch_of "$(systemctl show -p NextElapseUSecRealtime --value \
        "$timer" 2>/dev/null || true)")
done

if [ -n "$RUNNING_TIMERS" ]; then
    echo ""
    echo "Stopping running timers for the update..."
    for timer in $RUNNING_TIMERS; do
        systemctl stop "$timer" && echo "  [OK] stopped $timer"
    done
fi
# Taken after the stop, so a run that fired while DUE_AT was read counts
# as one from before the deploy.
QUIESCED_AT=$(date +%s)

# --- Apply code update (fast-forward only) ---

echo ""
echo "Applying code update..."
if ! sudo -u pi git merge --ff-only "$DEPLOY_SHA"; then
    echo "[FAIL] Fast-forward merge failed (local commits diverged from main)"
    echo "Resolve manually: git reset --hard origin/main"
    exit 1
fi
echo "[OK] Code updated to latest main"

# --- Create/update virtual environment ---

echo ""
echo "Setting up Python virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    sudo -u pi python3 -m venv "$VENV_DIR"
    echo "[OK] Virtual environment created"
else
    echo "[OK] Virtual environment exists"
fi

echo "Installing Python dependencies..."
sudo -u pi "$VENV_DIR/bin/pip" install --quiet --upgrade pip
sudo -u pi "$VENV_DIR/bin/pip" install --quiet -r requirements.txt
echo "[OK] Dependencies installed"

ln -sfn "$DEPLOY_DIR/pump.sh" /usr/local/bin/redhouse-pump
echo "[OK] redhouse-pump runs $DEPLOY_DIR/pump.sh"

# --- Validate configuration ---

echo ""
echo "Validating configuration..."

if [ ! -f "$DEPLOY_DIR/.env" ]; then
    echo "[FAIL] No .env file found at $DEPLOY_DIR/.env"
    echo "Copy .env.example and configure: cp .env.example .env"
    exit 1
fi

if [ ! -f "$DEPLOY_DIR/config/sensors.yaml" ]; then
    echo "[FAIL] No config/sensors.yaml found"
    echo "Copy sensors.yaml.example and configure with real sensor IDs"
    exit 1
fi

echo "[OK] .env and config/sensors.yaml exist"

# --- Create log directory ---

mkdir -p /var/log/redhouse
chown pi:pi /var/log/redhouse

# --- Install and restart systemd services ---

echo ""
echo "Installing systemd services..."
cp deployment/systemd/*.service /etc/systemd/system/
cp deployment/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
echo "[OK] Systemd services installed"

echo ""
echo "Restarting systemd timers..."
TIMERS=(
    "redhouse-temperature"
    "redhouse-weather"
    "redhouse-spot-prices"
    "redhouse-checkwatt"
    "redhouse-shelly-em3"
    "redhouse-windpower"
    "redhouse-aggregate-emeters-5min"
    "redhouse-aggregate-analytics-15min"
    "redhouse-aggregate-analytics-1hour"
    "redhouse-solar-prediction"
    "redhouse-generate-program"
    "redhouse-execute-program"
    "redhouse-health-check"
    "redhouse-program-check"
    "redhouse-program-fallback"
    "redhouse-backup"
)

# Restart only what was running before the deploy, and converge the
# enabled state onto it so a reboot reproduces exactly that. A stopped
# timer is a deliberate choice, often because wibatemp still owns it.
was_running() {
    case " $RUNNING_TIMERS " in
        *" $1 "*) return 0 ;;
        *) return 1 ;;
    esac
}

# These never touch a load: they watch, or write a file only redhouse
# reads. Without this the deploy that installs one disables it again,
# and health_check skips disabled timers, so nothing reports it.
ALWAYS_ON=(
    "redhouse-program-check"
    "redhouse-program-fallback"
)

is_always_on() {
    for timer in "${ALWAYS_ON[@]}"; do
        if [ "$timer" = "$1" ]; then
            return 0
        fi
    done
    return 1
}

should_run() {
    [ "$START_ALL" = true ] || was_running "$1.timer" || is_always_on "$1"
}

RUNNING=0
STOPPED=0
for timer in "${TIMERS[@]}"; do
    if should_run "$timer"; then
        systemctl enable "$timer.timer" 2>/dev/null
        systemctl restart "$timer.timer"
        echo "  [OK] $timer.timer"
        RUNNING=$((RUNNING + 1))
    else
        systemctl disable "$timer.timer" 2>/dev/null
        echo "  [SKIP] $timer.timer (not migrated, left stopped and disabled)"
        STOPPED=$((STOPPED + 1))
    fi
done

echo ""
echo "Timers running: $RUNNING    left stopped: $STOPPED"
if [ "$STOPPED" -gt 0 ]; then
    echo "Migrate one with: sudo systemctl enable --now redhouse-<name>.timer"
    echo "Disable its wibatemp cron line in the same step."
fi

BOOT_UNITS=(
    "redhouse-pump-restore"
)
# Run once at boot, never started here; they follow the executor timer
for unit in "${BOOT_UNITS[@]}"; do
    if should_run "redhouse-execute-program"; then
        action="enable"
    else
        action="disable"
    fi
    if systemctl "$action" "$unit.service"; then
        echo "  [OK] $unit.service: $action"
    else
        echo "  [WARN] could not $action $unit.service; the check below reports it"
    fi
done

# --- Verify every timer reached the state chosen above ---

echo ""
echo "Verifying timer states..."
STATES_OK=true
verify_timer_states || STATES_OK=false
verify_boot_units || STATES_OK=false
if [ "$STATES_OK" != true ]; then
    echo "[FAIL] Deploy finished with timers or boot units in the wrong state"
    rollback_hint
    exit 1
fi
echo "[OK] Every timer is in its intended state"

echo "Checking that no job ran off schedule during the deploy..."
if ! verify_no_offschedule_runs; then
    echo "[FAIL] A timer restart ran its job off schedule"
    rollback_hint
    exit 1
fi
echo "[OK] No job ran off schedule"

# --- Set up Grafana alerts ---

echo ""
echo "Setting up Grafana alerts..."
if sudo -u pi "$VENV_DIR/bin/python" -u deployment/setup_grafana_alerts.py --env production 2>/dev/null; then
    echo "[OK] Grafana alerts deployed"
else
    echo "[WARN] Grafana alert setup failed (non-fatal)"
fi

# --- Done ---

echo ""
echo "=================================================="
echo "Production Deployment Complete!"
echo "=================================================="
echo ""
echo "Pre-deploy commit: ${PRE_DEPLOY_COMMIT}"
echo "Current commit:    $(sudo -u pi git rev-parse HEAD)"
echo ""
rollback_hint
echo ""
systemctl list-timers redhouse-* --no-pager
echo ""
echo "Monitor: journalctl -u \"redhouse-*\" -f"
echo "Pump:    to hand the pump back to wibatemp, follow the Phase 6"
echo "         Rollback in docs/PRODUCTION_DEPLOYMENT_PLAN.md"
