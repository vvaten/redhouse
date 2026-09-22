#!/bin/bash
# Regression test for the is_always_on matcher in deploy_production.sh.
# The bug it guards: the deploy disables any timer that was not already
# running, so it installed redhouse-program-check and then disabled it.

set -u
FAILED=0

ALWAYS_ON=(
    "redhouse-program-check"
)

is_always_on() {
    for timer in "${ALWAYS_ON[@]}"; do
        if [ "$timer" = "$1" ]; then
            return 0
        fi
    done
    return 1
}

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

echo
if [ "$FAILED" -eq 0 ]; then
    echo "all assertions passed"
else
    echo "$FAILED assertion(s) failed"
    exit 1
fi
