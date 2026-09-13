#!/bin/bash
# Enable or disable staging systemd timers.
#
# Usage:
#   sudo deployment/staging_timers.sh start                    # Start all
#   sudo deployment/staging_timers.sh stop                     # Stop all
#   sudo deployment/staging_timers.sh start temperature        # Start one
#   sudo deployment/staging_timers.sh stop temperature         # Stop one
#   sudo deployment/staging_timers.sh status                   # Show status

set -e

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)"
    exit 1
fi

ACTION="${1:-status}"
SPECIFIC="${2:-}"

TIMERS=(
    "temperature"
    "weather"
    "spot-prices"
    "checkwatt"
    "shelly-em3"
    "windpower"
    "aggregate-emeters-5min"
    "aggregate-analytics-15min"
    "aggregate-analytics-1hour"
    "solar-prediction"
    "generate-program"
    "execute-program"
    "health-check"
    "backup"
)

validate_timer_name() {
    local name="$1"
    for timer in "${TIMERS[@]}"; do
        if [ "$timer" = "$name" ]; then
            return 0
        fi
    done
    echo "ERROR: Unknown timer '$name'"
    echo ""
    echo "Available timers:"
    for timer in "${TIMERS[@]}"; do
        echo "  $timer"
    done
    exit 1
}

case "$ACTION" in
    start)
        if [ -n "$SPECIFIC" ]; then
            validate_timer_name "$SPECIFIC"
            systemctl start "redhouse-staging-${SPECIFIC}.timer"
            echo "[OK] Started redhouse-staging-${SPECIFIC}.timer"
        else
            for timer in "${TIMERS[@]}"; do
                systemctl start "redhouse-staging-${timer}.timer" 2>/dev/null \
                    && echo "  [OK] redhouse-staging-${timer}.timer" \
                    || echo "  [SKIP] redhouse-staging-${timer}.timer (not installed)"
            done
        fi
        ;;
    stop)
        if [ -n "$SPECIFIC" ]; then
            validate_timer_name "$SPECIFIC"
            systemctl stop "redhouse-staging-${SPECIFIC}.timer"
            echo "[OK] Stopped redhouse-staging-${SPECIFIC}.timer"
        else
            for timer in "${TIMERS[@]}"; do
                systemctl stop "redhouse-staging-${timer}.timer" 2>/dev/null \
                    && echo "  [OK] redhouse-staging-${timer}.timer" \
                    || echo "  [SKIP] redhouse-staging-${timer}.timer (not running)"
            done
        fi
        ;;
    status)
        systemctl list-timers redhouse-staging-* --no-pager 2>/dev/null || echo "No staging timers active"
        ;;
    *)
        echo "Usage: $0 {start|stop|status} [timer-name]"
        echo ""
        echo "Examples:"
        echo "  $0 start                    # Start all staging timers"
        echo "  $0 stop                     # Stop all staging timers"
        echo "  $0 start temperature        # Start just temperature"
        echo "  $0 stop temperature         # Stop just temperature"
        echo "  $0 status                   # Show active staging timers"
        exit 1
        ;;
esac
