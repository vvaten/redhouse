#!/bin/bash
# Runs ci_gate and verify_timer_states, taken verbatim from the deploy
# script given as $1, against stubbed sudo, git, python3 and systemctl.

set -u
SCRIPT="$1"
FAILED=0

extract() {
    sed -n "/^$1() {/,/^}/p" "$SCRIPT"
}
eval "$(extract ci_gate)"
eval "$(extract verify_timer_states)"
if ! declare -F ci_gate > /dev/null || ! declare -F verify_timer_states > /dev/null; then
    echo "  FAIL  could not extract the functions from $SCRIPT"
    exit 1
fi

sudo() { shift 2; "$@"; }

expect() {
    local label="$1" want="$2" got="$3"
    if [ "$got" = "$want" ]; then
        echo "  PASS  $label (exit $got)"
    else
        echo "  FAIL  $label (exit $got, wanted $want)"
        FAILED=$((FAILED + 1))
    fi
}

echo "case: CI gate"
git() { echo 'import sys; sys.exit(0 if sys.argv[1] == "green" else 1)'; }
python3() { [ "$1" = "-c" ] && [ "$3" = "green" ]; }
ci_gate green > /dev/null; expect "green commit passes" 0 $?
ci_gate red > /dev/null; expect "red commit refuses" 1 $?
git() { return 128; }
ci_gate green > /dev/null; expect "unreadable checker refuses" 1 $?
git() { :; }
ci_gate green > /dev/null; expect "empty checker refuses" 1 $?

echo "case: timer verification"
TIMERS=("redhouse-a" "redhouse-b")
should_run() { [ "$1" = "redhouse-a" ]; }
declare -A ENABLED ACTIVE
systemctl() {
    local name="${2%.timer}"
    case "$1" in
        is-enabled) echo "${ENABLED[$name]}" ;;
        is-active) echo "${ACTIVE[$name]}" ;;
    esac
    [ "$1" = is-enabled ] && [ "${ENABLED[$name]}" = enabled ] && return 0
    [ "$1" = is-active ] && [ "${ACTIVE[$name]}" = active ] && return 0
    return 1
}
ENABLED=([redhouse-a]=enabled [redhouse-b]=disabled)
ACTIVE=([redhouse-a]=active [redhouse-b]=inactive)
verify_timer_states > /dev/null; expect "intended states pass" 0 $?
ACTIVE[redhouse-a]=inactive
verify_timer_states > /dev/null; expect "a stopped timer that should run fails" 1 $?
ACTIVE[redhouse-a]=active
ENABLED[redhouse-b]=enabled
verify_timer_states > /dev/null; expect "an enabled timer that should not run fails" 1 $?
ENABLED[redhouse-b]=disabled
ENABLED[redhouse-a]=not-found
verify_timer_states > /dev/null; expect "a missing unit fails" 1 $?

echo
if [ "$FAILED" -eq 0 ]; then
    echo "all assertions passed"
else
    echo "$FAILED assertion(s) failed"
    exit 1
fi
