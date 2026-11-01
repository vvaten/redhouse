#!/bin/bash
# Tests is_always_on, taken verbatim from the deploy script given as $1:
# it matches whole names in ALWAYS_ON only, and leaves its caller's
# $timer unchanged.

set -u
SCRIPT="$1"
FAILED=0

eval "$(sed -n '/^ALWAYS_ON=(/,/^)/p' "$SCRIPT")"
eval "$(sed -n '/^is_always_on() {/,/^}/p' "$SCRIPT")"
if ! declare -F is_always_on > /dev/null; then
    echo "  FAIL  could not extract is_always_on from $SCRIPT"
    exit 1
fi

check() {
    local label="$1" expect="$2" name="$3"
    if is_always_on "$name"; then
        got=yes
    else
        got=no
    fi
    if [ "$got" = "$expect" ]; then
        echo "  PASS  $label ($name -> $got)"
    else
        echo "  FAIL  $label ($name -> $got, wanted $expect)"
        FAILED=$((FAILED + 1))
    fi
}

echo "case: the watchdog is always on"
check "program-check found"   yes redhouse-program-check

echo "case: collectors are not, so the migration rule still governs them"
check "temperature not found" no  redhouse-temperature
check "generate not found"    no  redhouse-generate-program
check "execute not found"     no  redhouse-execute-program

echo "case: exact match only, no prefix or suffix"
check "with .timer not found" no  redhouse-program-check.timer
check "prefix not found"      no  redhouse-program
check "longer not found"      no  redhouse-program-check-extra

echo "case: empty argument"
check "empty not found"       no  ""

echo "case: the caller's loop variable is left alone"
timer="redhouse-pinglogger"
is_always_on "$timer" || true
if [ "$timer" = "redhouse-pinglogger" ]; then
    echo "  PASS  \$timer kept"
else
    echo "  FAIL  \$timer became $timer"
    FAILED=$((FAILED + 1))
fi

echo
if [ "$FAILED" -eq 0 ]; then
    echo "all assertions passed"
else
    echo "$FAILED assertion(s) failed"
    exit 1
fi
