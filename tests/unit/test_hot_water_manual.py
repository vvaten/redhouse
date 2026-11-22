"""Rule 1 and the manual paths: manual ON kept 30 min, manual OFF suspends
the rules 30 min, circulate and pause from the CLI, no ON when unsafe."""

import pytest

from src.control import hot_water_manual as hm
from src.control.hardware_implementations import RelayState

LIMITS = hm.RelayLimits(
    on_timer_s=180, manual_on_keep_s=1800, manual_off_suspend_s=1800, circulate_max_min=60
)
ON_TIMER = RelayState(True, True)
ON_MANUAL = RelayState(True, False)
OFF = RelayState(False, False)


def act(rule_on, relay, mem, now, allowed=True):
    return hm.relay_action(rule_on, "rule", relay, mem, now, LIMITS, allowed)


class TestDefaultOff:
    def test_off_unless_a_rule_wants_on(self):
        assert act(False, ON_TIMER, hm.Memory(), 0).command == "off"
        assert act(False, OFF, hm.Memory(), 0).command == "none"

    def test_on_renews_the_timer_each_tick(self):
        mem = hm.Memory()
        assert act(True, OFF, mem, 0).command == "on"
        hm.record(mem, hm.Action("on", "rule"), ON_TIMER, 0)
        assert act(True, ON_TIMER, mem, 60).command == "on"

    def test_unreadable_relay_does_nothing(self):
        assert act(True, None, hm.Memory(), 0).command == "none"


class TestManualOn:
    def test_kept_30_min_then_off(self):
        mem = hm.Memory()
        assert act(False, ON_MANUAL, mem, 0).command == "none"
        assert act(False, ON_MANUAL, mem, 1799).command == "none"
        assert act(False, ON_MANUAL, mem, 1800).command == "off"

    def test_a_failed_off_does_not_restart_the_30_min(self):
        mem = hm.Memory()
        act(False, ON_MANUAL, mem, 0)
        off = act(False, ON_MANUAL, mem, 1800)
        hm.record(mem, off, None, 1800)
        assert act(False, ON_MANUAL, mem, 1860).command == "off"
        hm.record(mem, off, OFF, 1860)
        assert mem.manual_on_since is None

    def test_pause_switches_off_a_manual_on(self):
        mem = hm.Memory()
        hm.apply_request(mem, {"action": "pause", "minutes": 10}, 0, LIMITS)
        assert act(False, ON_MANUAL, mem, 60).command == "off"

    def test_a_manual_on_after_a_reboot_counts_from_first_seen(self):
        mem = hm.Memory()
        assert act(True, ON_MANUAL, mem, 5000).reason == "manual ON kept"


class TestManualOff:
    def test_suspends_the_rules_for_30_min(self):
        mem = hm.Memory()
        hm.record(mem, hm.Action("on", "rule"), ON_TIMER, 0)
        assert act(True, OFF, mem, 60).command == "none"
        assert act(True, OFF, mem, 1859).reason == "suspended after a manual OFF"
        assert act(True, OFF, mem, 1861).command == "on"

    def test_a_timer_that_ran_out_is_no_manual_off(self):
        mem = hm.Memory()
        hm.record(mem, hm.Action("on", "rule"), ON_TIMER, 0)
        assert act(True, OFF, mem, 170).command == "on"


class TestRequests:
    def test_circulate_runs_its_minutes_and_is_no_manual_on(self):
        mem = hm.Memory()
        assert (
            hm.apply_request(mem, {"action": "circulate", "minutes": 30}, 0, LIMITS)
            == "circulate 30 min"
        )
        assert act(False, OFF, mem, 60).command == "on"
        hm.record(mem, hm.Action("on", "circulate"), ON_TIMER, 60)
        assert act(False, ON_TIMER, mem, 1799).command == "on"
        assert act(False, ON_TIMER, mem, 1800).command == "off"

    def test_circulate_is_capped(self):
        mem = hm.Memory()
        assert (
            hm.apply_request(mem, {"action": "circulate", "minutes": 600}, 0, LIMITS)
            == "circulate 60 min"
        )

    def test_pause_stops_everything(self):
        mem = hm.Memory()
        hm.apply_request(mem, {"action": "circulate", "minutes": 30}, 0, LIMITS)
        hm.apply_request(mem, {"action": "pause", "minutes": 10}, 60, LIMITS)
        assert act(True, ON_TIMER, mem, 120).command == "off"
        assert act(True, OFF, mem, 659).reason == "paused"
        assert act(True, OFF, mem, 661).command == "on"

    @pytest.mark.parametrize(
        "request_",
        [
            {"action": "circulate"},
            {"action": "x", "minutes": 1},
            {"action": "pause", "minutes": -1},
        ],
    )
    def test_a_bad_request_is_refused(self, request_):
        with pytest.raises(ValueError):
            hm.apply_request(hm.Memory(), request_, 0, LIMITS)


class TestUnsafe:
    def test_no_on_before_the_clock_syncs_or_on_stale_data(self):
        decision = act(True, OFF, hm.Memory(), 0, allowed=False)
        assert decision.command == "none" and "clock" in decision.reason

    def test_circulate_also_waits_for_safe_inputs(self):
        mem = hm.Memory()
        hm.apply_request(mem, {"action": "circulate", "minutes": 5}, 0, LIMITS)
        assert act(False, ON_TIMER, mem, 60, allowed=False).command == "off"
