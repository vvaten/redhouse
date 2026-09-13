#!/bin/bash
# Regression test for the was_running matcher in deploy_production.sh.
# The bug it guards: RUNNING_TIMERS comes from a pipeline and is newline
# separated, while the matcher compares on surrounding spaces, so nothing
# ever matched and every timer was disabled.

set -u
FAILED=0

was_running() {
    case " $RUNNING_TIMERS " in
        *" $1 "*) return 0 ;;
        *) return 1 ;;
    esac
}

check() {
    local label="$1" expect="$2" name="$3"
    if was_running "$name"; then
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

echo "case: real capture shape, newline separated then normalised"
RUNNING_TIMERS=$(printf 'redhouse-checkwatt.timer\nredhouse-temperature.timer\n' | tr '\n' ' ')
check "checkwatt found"      yes redhouse-checkwatt.timer
check "temperature found"    yes redhouse-temperature.timer
check "weather not found"    no  redhouse-weather.timer

echo "case: raw newlines, the shape that caused the incident"
RUNNING_TIMERS=$(printf 'redhouse-checkwatt.timer\nredhouse-temperature.timer\n')
if was_running redhouse-checkwatt.timer; then
    echo "  note  newline shape happens to match"
else
    echo "  note  newline shape does NOT match, which was the bug"
fi

echo "case: empty"
RUNNING_TIMERS=""
check "nothing found" no redhouse-temperature.timer

echo "case: substring must not match a longer name"
RUNNING_TIMERS="redhouse-aggregate-analytics-15min.timer "
check "15min found"          yes redhouse-aggregate-analytics-15min.timer
check "1hour not found"      no  redhouse-aggregate-analytics-1hour.timer
check "prefix not found"     no  redhouse-aggregate.timer

echo
if [ "$FAILED" -eq 0 ]; then
    echo "all assertions passed"
else
    echo "$FAILED assertion(s) failed"
    exit 1
fi
