#!/bin/bash
#
# Smart deployment script that waits for the next optimal deployment window
#
# Optimal windows: :06-:09, :21-:24, :36-:39, :51-:54 (avoids heating execution and aggregation)
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

# Optimal deployment windows (start minute of each 4-minute window)
OPTIMAL_WINDOWS=(6 21 36 51)

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

    # Convert to seconds and subtract current seconds to align to exact minute
    echo $((minutes_to_wait * 60 - current_second))
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
    [ -z "$current_minute" ] && current_minute=0

    for window_start in "${OPTIMAL_WINDOWS[@]}"; do
        local window_end=$((window_start + 3))
        if [ "$current_minute" -ge "$window_start" ] && [ "$current_minute" -le "$window_end" ]; then
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
    echo "Safe windows (4 minutes each):"
    echo "  :06-:09  (after aggregation, before next collection)"
    echo "  :21-:24  (after aggregation, before next collection)"
    echo "  :36-:39  (after aggregation, before next collection)"
    echo "  :51-:54  (after aggregation, before next collection)"
    echo ""
    echo "Avoid these times:"
    echo "  :00, :15, :30, :45  (heating program execution)"
    echo "  :00, :05, :10, etc. (5-min aggregation + Shelly EM3)"
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
