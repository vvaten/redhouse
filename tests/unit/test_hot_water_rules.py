"""The relay rules 2, 3, 4, 4b and 7, the modes and the priority merge."""

import datetime

from src.common.local_time import HELSINKI
from src.control import hot_water_boost as hb
from src.control import hot_water_rules as hr

H = 3600
BOOST = hb.BoostSettings(
    anchor_utc=hb.parse_utc("2026-11-27T20:00:00Z"),
    interval_s=14 * 86400,
    slot_s=3 * H,
    trigger_attempt_s=30 * 60,
    retry_gap_s=30 * 60,
    unexpected_after_s=36 * H,
)
EXPIRY = hb.parse_utc("2026-12-11T20:00:00Z")
SETTINGS = {
    "boost": BOOST,
    "flush": {"start_c": 65.0, "minutes": 45},
    "preheat": {"price_c_kwh": 20.0, "horizon_h": 12, "reach_c": 2.0},
    "pre_on": {"lead_min": 20, "max_extra_c_kwh": 3.0},
    "comfort": {
        "minutes": 15,
        "price_c_kwh": 15.0,
        "slots": {"fri": "21:40", "sat": "19:30", "other": "21:00"},
    },
}
ALL_ACT = {r: "act" for r in hr.RULES}


def local(text: str) -> int:
    naive = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")
    return int(HELSINKI.localize(naive).timestamp())


def ctx(
    now,
    records=(),
    bottom=46.0,
    top=50.0,
    prices=None,
    program=None,
    open_cycle=None,
    last=None,
    level=42.7,
):
    return hr.Context(
        now=now,
        top=top,
        bottom=bottom,
        start_level=level,
        boost=hb.status(list(records), now, BOOST),
        open_cycle=open_cycle,
        last_cycle_start=last,
        prices={} if prices is None else prices,
        program={} if program is None else program,
        settings=SETTINGS,
    )


def flat(now, cents, hours=24):
    first = hr.hour_of(now)
    return {first + k * H: cents / 100 for k in range(-2, hours)}


class TestBoostTrigger:
    def test_on_in_the_slot_until_the_attempt_ends_then_a_retry(self):
        state = hr.RuleState()
        assert hr.boost_trigger(ctx(EXPIRY + 60), state) is not None
        assert hr.boost_trigger(ctx(EXPIRY + 29 * 60), state) is not None
        assert hr.boost_trigger(ctx(EXPIRY + 31 * 60), state) is None
        assert hr.boost_trigger(ctx(EXPIRY + 61 * 60), state) is not None

    def test_off_once_a_run_starts(self):
        state = hr.RuleState()
        hr.boost_trigger(ctx(EXPIRY + 60), state)
        run = hb.Record(EXPIRY + 300, EXPIRY + 300, 45.0, 47.0, False, hb.BOOST, EXPIRY)
        assert hr.boost_trigger(ctx(EXPIRY + 360, open_cycle=run, last=run.start), state) is None

    def test_a_stall_near_51_c_is_retried(self):
        state = hr.RuleState()
        stalled = hb.label([hb.Record(EXPIRY + 300, EXPIRY + 1500, 45.0, 51.0, False)], BOOST)
        want = hr.boost_trigger(ctx(EXPIRY + 3600, stalled, last=EXPIRY + 300), state)
        assert want is not None and "stalled" in want.reason

    def test_after_a_stall_in_the_attempt_the_retry_waits_the_gap(self):
        state = hr.RuleState()
        assert hr.boost_trigger(ctx(EXPIRY + 60), state) is not None
        stalled = hb.label([hb.Record(EXPIRY + 300, EXPIRY + 900, 45.0, 51.0, False)], BOOST)
        assert hr.boost_trigger(ctx(EXPIRY + 1200, stalled, last=EXPIRY + 300), state) is None
        assert hr.boost_trigger(ctx(EXPIRY + 3660, stalled, last=EXPIRY + 300), state) is not None

    def test_nothing_after_the_slot(self):
        assert hr.boost_trigger(ctx(EXPIRY + 3 * H + 60), hr.RuleState()) is None

    def test_it_triggers_in_program_on_too(self):
        program = {hr.hour_of(EXPIRY): "ON"}
        assert hr.boost_trigger(ctx(EXPIRY + 60, program=program), hr.RuleState()) is not None


class TestFlush:
    def test_from_65_c_in_a_boost_for_45_min(self):
        state = hr.RuleState()
        boost = hb.Record(EXPIRY, EXPIRY, 45.0, 65.0, False, hb.BOOST, EXPIRY)
        assert hr.flush(ctx(EXPIRY + H, top=64.9, open_cycle=boost), state) is None
        assert hr.flush(ctx(EXPIRY + H + 60, top=65.0, open_cycle=boost), state) is not None
        assert hr.flush(ctx(EXPIRY + H + 44 * 60, top=69.0), state) is not None
        assert hr.flush(ctx(EXPIRY + H + 46 * 60, top=69.0), state) is None

    def test_not_in_a_regular_run(self):
        run = hb.Record(EXPIRY, EXPIRY, 45.0, 66.0, False, hb.REGULAR)
        assert hr.flush(ctx(EXPIRY + 60, top=66.0, open_cycle=run), hr.RuleState()) is None


class TestPreheat:
    def _prices(self, now, dear_from):
        """10 c, 5 c in the second hour from now, 35 c from dear_from on."""
        prices = flat(now, 10.0)
        for k in range(dear_from, dear_from + 6):
            prices[hr.hour_of(now) + k * H] = 0.35
        prices[hr.hour_of(now) + 2 * H] = 0.05
        return prices

    def test_the_cheapest_hour_before_a_dear_window(self):
        """46.0 C reaches the 42.7 C start 7.3 h on, inside the dear window."""
        now = local("2026-12-01 12:05")
        c = ctx(now, bottom=46.0, prices=self._prices(now, 7))
        assert hr.preheat_hour(c) == hr.hour_of(now) + 2 * H

    def test_on_in_that_hour_when_the_bottom_is_within_reach(self):
        """44.5 C reaches the start 4 h on; the cheapest hour before is now."""
        now = local("2026-12-01 14:05")
        prices = {**flat(now, 10.0), hr.hour_of(now): 0.04}
        for k in range(3, 8):
            prices[hr.hour_of(now) + k * H] = 0.35
        state = hr.RuleState()
        assert hr.preheat(ctx(now, bottom=44.5, prices=prices), state) is not None
        assert hr.preheat(ctx(now + 600, bottom=44.4, prices=prices), state) is not None
        assert hr.preheat(ctx(now + 900, bottom=44.4, prices=prices, last=now + 800), state) is None

    def test_not_out_of_reach(self):
        now = local("2026-12-01 14:05")
        prices = {**flat(now, 10.0), hr.hour_of(now): 0.04}
        for k in range(5, 11):
            prices[hr.hour_of(now) + k * H] = 0.35
        assert hr.preheat(ctx(now, bottom=45.0, prices=prices), hr.RuleState()) is None

    def test_an_evu_block_is_a_dear_window(self):
        now = local("2026-12-01 12:05")
        program = {hr.hour_of(now) + k * H: "EVU" for k in range(7, 13)}
        c = ctx(now, bottom=46.0, prices=flat(now, 10.0), program=program)
        assert hr.preheat_hour(c) is not None

    def test_no_preheat_without_a_dear_window(self):
        now = local("2026-12-01 12:05")
        assert hr.preheat_hour(ctx(now, bottom=46.0, prices=flat(now, 10.0))) is None

    def test_never_while_a_pending_boost_blocks_runs(self):
        state = hr.RuleState()
        prices = {**flat(EXPIRY, 10.0), hr.hour_of(EXPIRY): 0.04}
        for k in range(3, 8):
            prices[hr.hour_of(EXPIRY) + k * H] = 0.35
        assert hr.preheat(ctx(EXPIRY + 60, bottom=44.5, prices=prices), state) is None
        after_slot = ctx(EXPIRY + 3 * H + 60, bottom=44.5, prices=prices)
        assert not after_slot.boost.blocks_runs


class TestPreOn:
    def test_on_lead_min_before_a_cheap_on_block(self):
        block = local("2026-12-02 03:00")
        program = {block - H: "ALE", block: "ON"}
        prices = {block - H: 0.07, block: 0.05}
        state = hr.RuleState()
        assert (
            hr.pre_on(ctx(block - 21 * 60, bottom=44.0, prices=prices, program=program), state)
            is None
        )
        assert (
            hr.pre_on(ctx(block - 20 * 60, bottom=44.0, prices=prices, program=program), state)
            is not None
        )

    def test_not_when_the_hour_before_is_clearly_dearer(self):
        block = local("2026-12-02 03:00")
        program = {block - H: "ALE", block: "ON"}
        prices = {block - H: 0.10, block: 0.05}
        assert (
            hr.pre_on(
                ctx(block - 10 * 60, bottom=44.0, prices=prices, program=program), hr.RuleState()
            )
            is None
        )

    def test_a_block_from_a_quarter_inside_the_hour(self):
        """ON from 03:15: the lead from 02:55 falls in the 02:00 hour."""
        block = local("2026-12-02 03:15")
        program = {block - H: "ALE", block: "ON", block + 1800: "ALE"}
        prices = {hr.hour_of(block) - H: 0.07, hr.hour_of(block): 0.05}
        c = ctx(block - 20 * 60, bottom=44.0, prices=prices, program=program)
        assert hr.on_block_start(c) == block
        assert hr.pre_on(c, hr.RuleState()) is not None

    def test_no_block_while_the_pump_is_on(self):
        block = local("2026-12-02 03:15")
        program = {block - H: "ON", block: "ON"}
        assert hr.on_block_start(ctx(block - 600, program=program)) is None

    def test_an_evu_quarter_makes_its_hour_dear(self):
        h = local("2026-12-02 17:00")
        c = ctx(h - H, prices={h: 0.05}, program={h: "ALE", h + 2700: "EVU"})
        assert hr._dear(c, h) and not hr._dear(c, h - H)

    def test_not_when_the_tank_is_far_from_its_start(self):
        block = local("2026-12-02 03:00")
        program = {block - H: "ALE", block: "ON"}
        prices = {block - H: 0.05, block: 0.05}
        assert (
            hr.pre_on(
                ctx(block - 10 * 60, bottom=48.0, prices=prices, program=program), hr.RuleState()
            )
            is None
        )


class TestComfort:
    def test_friday_slot_when_cheap(self):
        now = local("2026-12-04 21:45")
        assert hr.comfort(ctx(now, prices=flat(now, 10.0)), hr.RuleState()) is not None
        assert hr.comfort(ctx(now + 15 * 60, prices=flat(now, 10.0)), hr.RuleState()) is None

    def test_never_in_a_dear_hour(self):
        now = local("2026-12-05 19:35")
        assert hr.comfort(ctx(now, prices=flat(now, 16.0)), hr.RuleState()) is None

    def test_skipped_on_a_low_tank_in_a_dear_window(self):
        now = local("2026-12-07 21:05")
        program = {hr.hour_of(now): "EVU"}
        c = ctx(now, bottom=43.0, prices=flat(now, 10.0), program=program)
        assert hr.comfort(c, hr.RuleState()) is None


class TestMerge:
    def test_priority_flush_over_trigger(self):
        boost = hb.Record(EXPIRY, EXPIRY, 45.0, 65.0, False, hb.BOOST, EXPIRY)
        stalled_free = ctx(EXPIRY + 60, top=66.0, open_cycle=boost)
        decision = hr.decide(stalled_free, hr.RuleState(), ALL_ACT)
        assert decision.on and decision.rule == "flush"

    def test_dry_run_reports_but_does_not_switch(self):
        modes = {**ALL_ACT, "boost_trigger": "dry_run"}
        decision = hr.decide(ctx(EXPIRY + 60), hr.RuleState(), modes)
        assert not decision.on and "boost_trigger" in decision.would

    def test_off_is_not_evaluated(self):
        modes = {**ALL_ACT, "boost_trigger": "off"}
        decision = hr.decide(ctx(EXPIRY + 60), hr.RuleState(), modes)
        assert not decision.on and decision.would == {}

    def test_default_off(self):
        decision = hr.decide(ctx(local("2026-12-01 12:00")), hr.RuleState(), ALL_ACT)
        assert not decision.on and decision.rule == "default_off"
