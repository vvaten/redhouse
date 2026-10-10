#!/bin/bash
# Runs ci_gate and verify_timer_states, taken verbatim from the deploy
# script given as $1, against stubbed sudo, git, python3 and systemctl.

set -u
SCRIPT="$1"
FAILED=0

extract() {
    sed -n "/^$1() {/,/^}/p" "$SCRIPT"
}
for fn in ci_gate verify_timer_states epoch_of verify_no_offschedule_runs; do
    eval "$(extract "$fn")"
    if ! declare -F "$fn" > /dev/null; then
        echo "  FAIL  could not extract $fn from $SCRIPT"
        exit 1
    fi
done
eval "$(sed -n '/^FIRES_ON_RESTART=(/,/^)/p' "$SCRIPT")"

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

echo "case: no job ran off schedule"
TIMERS=("redhouse-a" "redhouse-b" "redhouse-temperature")
declare -A STARTED
systemctl() { echo "${STARTED[${5%.service}]:-}"; }
# Stamps in systemctl's own format, which epoch_of must parse.
QUIESCED_AT=$(epoch_of "Sat 2026-10-10 13:07:10 EEST")
declare -A DUE_AT
DUE_AT[redhouse-a.timer]=$(epoch_of "Sat 2026-10-10 18:00:00 EEST")
DUE_AT[redhouse-b.timer]=$(epoch_of "Sat 2026-10-10 13:08:00 EEST")
DUE_AT[redhouse-temperature.timer]=$(epoch_of "Sat 2026-10-10 13:07:50 EEST")
if [ -z "${DUE_AT[redhouse-a.timer]}" ]; then
    echo "  FAIL  epoch_of cannot parse systemctl's timestamp format"
    FAILED=$((FAILED + 1))
fi
STARTED=([redhouse-a]="Fri 2026-10-09 18:00:00 EEST" [redhouse-b]="" [redhouse-temperature]="")
verify_no_offschedule_runs > /dev/null; expect "runs before the deploy pass" 0 $?
STARTED[redhouse-a]="Sat 2026-10-10 13:07:39 EEST"
verify_no_offschedule_runs > /dev/null; expect "a run before it was due fails" 1 $?
STARTED[redhouse-a]="Sat 2026-10-10 13:07:10 EEST"
verify_no_offschedule_runs > /dev/null; expect "a run in the quiesce second passes" 0 $?
STARTED[redhouse-a]="Fri 2026-10-09 18:00:00 EEST"
STARTED[redhouse-b]="Sat 2026-10-10 13:08:00 EEST"
verify_no_offschedule_runs > /dev/null; expect "a run on its schedule passes" 0 $?
DUE_AT[redhouse-a.timer]=$(epoch_of "Sat 2026-10-10 13:05:00 EEST")
STARTED[redhouse-a]="Sat 2026-10-10 13:07:39 EEST"
verify_no_offschedule_runs > /dev/null; expect "a catch-up that was due passes" 0 $?
STARTED[redhouse-temperature]="Sat 2026-10-10 13:07:26 EEST"
verify_no_offschedule_runs > /dev/null; expect "temperature may run on restart" 0 $?
unset 'DUE_AT[redhouse-b.timer]'
STARTED[redhouse-b]="Sat 2026-10-10 13:07:30 EEST"
out=$(verify_no_offschedule_runs); status=$?
expect "a timer with no due time only warns" 0 $status
case "$out" in *WARN*) ;; *) echo "  FAIL  no warning printed"; FAILED=$((FAILED + 1)) ;; esac
STARTED[redhouse-b]="garbled"
out=$(verify_no_offschedule_runs); status=$?
case "$out" in *"cannot read"*) ;; *) echo "  FAIL  unparsable stamp not reported"; FAILED=$((FAILED + 1)) ;; esac
STARTED[redhouse-b]=""

echo "case: across the October DST change"
QUIESCED_AT=$(epoch_of "Sun 2026-10-25 03:59:30 EEST")
DUE_AT[redhouse-a.timer]=$(epoch_of "Sun 2026-10-25 03:00:30 EET")
STARTED[redhouse-a]="Sun 2026-10-25 03:00:10 EET"
verify_no_offschedule_runs > /dev/null; expect "a run in the repeated hour before it was due fails" 1 $?

echo
if [ "$FAILED" -eq 0 ]; then
    echo "all assertions passed"
else
    echo "$FAILED assertion(s) failed"
    exit 1
fi
