#!/bin/bash
#
# Smart deployment script that waits for the next optimal deployment window
#
# Optimal windows: :06:10-:08:30, :21:10-:23:30, :36:10-:38:30, :51:10-:53:30
# (start 10s after to let Shelly EM3 collector run, end 30s before next collection)
# Deployment should complete in 2-3 minutes
#
# Usage:
#   ./deploy_smart.sh              # Wait for next window, then deploy
#   ./deploy_smart.sh --now        # Deploy immediately (skip window check)
#   ./deploy_smart.sh --schedule   # Show next window and exit
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_SCRIPT="${SCRIPT_DIR}/deploy.sh"

# Optimal deployment windows (start minute of each window)
OPTIMAL_WINDOWS=(6 21 36 51)
# Window timing adjustments
WINDOW_START_OFFSET=10  # Start 10 seconds after the minute
WINDOW_END_OFFSET=30    # End 30 seconds before the end minute

# Function to get next optimal window
get_next_window() {
    local current_minute=$(date +%M | sed 's/^0//')
    local current_hour=$(date +%H)
    local current_day=$(date +%d)

    # Find next window in current hour
    for window_start in "${OPTIMAL_WINDOWS[@]}"; do
        if [ "$current_minute" -lt "$window_start" ]; then
            echo "$window_start"
            return
        fi
    done

    # No more windows in current hour, return first window of next hour
    echo "6"
}

# Function to calculate wait time in seconds
calculate_wait_time() {
    local current_minute=$(date +%M | sed 's/^0*//')
    local current_second=$(date +%S | sed 's/^0*//')
    local next_window=$1

    # Handle empty values
    [ -z "$current_minute" ] && current_minute=0
    [ -z "$current_second" ] && current_second=0

    local minutes_to_wait

    if [ "$current_minute" -lt "$next_window" ]; then
        # Next window is in current hour
        minutes_to_wait=$((next_window - current_minute))
    else
        # Next window is in next hour
        minutes_to_wait=$((60 - current_minute + next_window))
    fi

    # Convert to seconds, subtract current seconds, and add the start offset
    # to wait until :XX:10 instead of :XX:00
    echo $((minutes_to_wait * 60 - current_second + WINDOW_START_OFFSET))
}

# Function to format seconds as human readable time
format_duration() {
    local seconds=$1
    local minutes=$((seconds / 60))
    local remaining_seconds=$((seconds % 60))

    if [ "$minutes" -gt 0 ]; then
        echo "${minutes}m ${remaining_seconds}s"
    else
        echo "${remaining_seconds}s"
    fi
}

# Function to check if we're in an optimal window
in_optimal_window() {
    local current_minute=$(date +%M | sed 's/^0*//')
    local current_second=$(date +%S | sed 's/^0*//')
    [ -z "$current_minute" ] && current_minute=0
    [ -z "$current_second" ] && current_second=0

    for window_start in "${OPTIMAL_WINDOWS[@]}"; do
        # Window: :06:10 to :08:30 (start minute + 0:10, end minute + 2:30)
        local window_end=$((window_start + 2))

        # Check if in start minute and after start offset
        if [ "$current_minute" -eq "$window_start" ] && [ "$current_second" -ge "$WINDOW_START_OFFSET" ]; then
            return 0
        fi
        # Check if in middle minutes
        if [ "$current_minute" -gt "$window_start" ] && [ "$current_minute" -lt "$window_end" ]; then
            return 0
        fi
        # Check if in end minute and before end offset
        if [ "$current_minute" -eq "$window_end" ] && [ "$current_second" -le "$((60 - WINDOW_END_OFFSET))" ]; then
            return 0
        fi
    done
    return 1
}

# Function to show deployment windows
show_schedule() {
    echo "Optimal Deployment Windows"
    echo "============================"
    echo ""
    echo "Safe windows (2m 20s each, adjusted for collectors):"
    echo "  :06:10-:08:30  (after Shelly EM3, before next collection)"
    echo "  :21:10-:23:30  (after Shelly EM3, before next collection)"
    echo "  :36:10-:38:30  (after Shelly EM3, before next collection)"
    echo "  :51:10-:53:30  (after Shelly EM3, before next collection)"
    echo ""
    echo "Avoid these times:"
    echo "  :00, :15, :30, :45  (heating program execution)"
    echo "  :00, :05, :10, etc. (5-min aggregation)"
    echo "  :X6:00-:X6:10       (Shelly EM3 collection)"
    echo "  :01, :06, :11, etc. (CheckWatt collection)"
    echo ""

    local current_time=$(date +"%H:%M:%S")
    local next_window=$(get_next_window)
    local wait_seconds=$(calculate_wait_time "$next_window")
    local wait_duration=$(format_duration "$wait_seconds")
    local next_time=$(date -d "+${wait_seconds} seconds" +"%H:%M" 2>/dev/null || date -v "+${wait_seconds}S" +"%H:%M")

    echo "Current time: ${current_time}"

    if in_optimal_window; then
        echo "Status: IN OPTIMAL WINDOW - SAFE TO DEPLOY NOW"
    else
        echo "Next window: ${next_time} (in ${wait_duration})"
    fi
}

# Parse command line arguments
DEPLOY_NOW=false
SHOW_SCHEDULE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --now)
            DEPLOY_NOW=true
            shift
            ;;
        --schedule)
            SHOW_SCHEDULE=true
            shift
            ;;
        *)
            echo "ERROR: Unknown option: $1"
            echo "Usage: $0 [--now|--schedule]"
            exit 1
            ;;
    esac
done

# Show schedule and exit if requested
if [ "$SHOW_SCHEDULE" = true ]; then
    show_schedule
    exit 0
fi

# Check if deploy script exists
if [ ! -f "$DEPLOY_SCRIPT" ]; then
    echo "ERROR: Deploy script not found at $DEPLOY_SCRIPT"
    exit 1
fi

# Deploy immediately if requested
if [ "$DEPLOY_NOW" = true ]; then
    echo "Deploying immediately (skipping window check)..."
    exec "$DEPLOY_SCRIPT"
fi

# Show current status
show_schedule
echo ""

# Check if we're already in an optimal window
if in_optimal_window; then
    echo "Currently in optimal deployment window!"
    echo "Starting deployment now..."
    echo ""
    exec "$DEPLOY_SCRIPT"
fi

# Calculate wait time
next_window=$(get_next_window)
wait_seconds=$(calculate_wait_time "$next_window")
wait_duration=$(format_duration "$wait_seconds")
next_time=$(date -d "+${wait_seconds} seconds" +"%H:%M:%S" 2>/dev/null || date -v "+${wait_seconds}S" +"%H:%M:%S")

echo ""
echo "Waiting for next optimal window..."
echo "Deployment will start at: ${next_time} (in ${wait_duration})"
echo ""
echo "Press Ctrl+C to cancel"
echo ""

# Countdown with progress
remaining=$wait_seconds
while [ $remaining -gt 0 ]; do
    mins=$((remaining / 60))
    secs=$((remaining % 60))
    printf "\rTime remaining: %02d:%02d " $mins $secs
    sleep 1
    remaining=$((remaining - 1))
done

echo ""
echo ""
echo "Optimal window reached! Starting deployment..."
echo ""

# Execute deployment
exec "$DEPLOY_SCRIPT"
