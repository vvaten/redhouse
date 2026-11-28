"""The legionella boost timer: expiries in UTC, labels, missed, incomplete
and unexpected boosts, the wrong-anchor relabel and the anchor file."""

import datetime

import pytest

from src.common.local_time import HELSINKI
from src.control import hot_water_boost as hb

H = 3600
DAY = 86400
SECTION = {
    "anchor_utc": "2026-11-27T20:00:00Z",
    "interval_days": 14,
    "slot_minutes": 180,
    "trigger_attempt_min": 30,
    "retry_gap_min": 30,
    "unexpected_after_h": 36,
}


def utc(text: str) -> int:
    return hb.parse_utc(text)


def local(text: str) -> int:
    naive = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")
    return int(HELSINKI.localize(naive).timestamp())


def settings(anchor: str = "2026-11-27T20:00:00Z", days: float = 14) -> hb.BoostSettings:
    return hb.load_boost_settings({**SECTION, "interval_days": days}, utc(anchor))


def rec(start: int, peak: float, start_top: float = 45.0, heater: bool = False) -> hb.Record:
    return hb.Record(start, start + 30 * 60, start_top, peak, heater)


class TestExpiries:
    def test_the_first_expiry_is_one_interval_after_the_restart(self):
        s = settings()
        assert hb.expiry_at_or_before(utc("2026-12-11T19:59:00Z"), s) is None
        assert hb.expiry_at_or_before(utc("2026-12-11T20:00:00Z"), s) == utc("2026-12-11T20:00:00Z")
        assert hb.next_expiry(utc("2026-12-01T00:00:00Z"), s) == utc("2026-12-11T20:00:00Z")

    def test_nov_16_restart_with_6_days_expires_sun_nov_22(self):
        s = settings("2026-11-16T05:00:00Z", 6)
        assert hb.next_expiry(utc("2026-11-21T00:00:00Z"), s) == utc("2026-11-22T05:00:00Z")

    def test_summer_keeps_utc_so_a_winter_22_00_anchor_expires_at_23_00(self):
        s = settings("2026-11-27T20:00:00Z", 14)
        e = hb.next_expiry(local("2027-06-01 00:00"), s)
        assert datetime.datetime.fromtimestamp(e, HELSINKI).hour == 23

    def test_slots_between(self):
        s = settings()
        e = utc("2026-12-11T20:00:00Z")
        assert hb.slots_between(e - H, e + H, s, 7200) == [(e, e + 7200)]
        assert hb.slots_between(e + 7200, e + 3 * H, s, 7200) == []


class TestLabels:
    def test_the_nov_22_boost_is_expected_on_the_nov_16_anchor(self):
        s = settings("2026-11-16T05:00:00Z", 6)
        run = rec(local("2026-11-22 00:04"), 51.0)
        boost = rec(local("2026-11-22 09:28"), 69.2)
        labelled = hb.label([run, boost], s)
        assert [r.kind for r in labelled] == [hb.REGULAR, hb.BOOST]
        assert labelled[1].expiry == utc("2026-11-22T05:00:00Z") and not labelled[1].unexpected

    def test_on_the_old_grid_the_nov_22_boost_is_unexpected(self):
        s = settings("2026-10-09T08:00:00Z", 14)
        labelled = hb.label([rec(local("2026-11-22 09:28"), 69.2)], s)
        assert labelled[0].kind == hb.BOOST and labelled[0].unexpected

    def test_the_first_cycle_after_the_expiry_is_the_boost_whatever_its_peak(self):
        s = settings()
        e = utc("2026-12-11T20:00:00Z")
        labelled = hb.label([rec(e + H, 50.5), rec(e + 5 * H, 51.0)], s)
        assert labelled[0].kind == hb.BOOST and labelled[0].stalled
        assert labelled[1].kind == hb.REGULAR

    def test_a_hot_start_is_a_heater_run(self):
        labelled = hb.label([rec(utc("2026-12-01T00:00:00Z"), 66.0, start_top=65.0)], settings())
        assert labelled[0].kind == hb.HEATER

    def test_a_used_expiry_is_not_taken_again(self):
        s = settings()
        e = utc("2026-12-11T20:00:00Z")
        labelled = hb.label([rec(e + H, 51.0)], s, frozenset({e}))
        assert labelled[0].kind == hb.REGULAR


class TestStatus:
    def test_pending_from_the_expiry_until_a_cycle(self):
        s = settings()
        e = utc("2026-12-11T20:00:00Z")
        st = hb.status([], e + 60, s)
        assert st.pending and st.in_slot and st.blocks_runs
        boost = hb.label([rec(e + 600, 70.0)], s)
        st = hb.status(boost, e + H, s)
        assert not st.pending and st.boost is not None

    def test_the_slot_end_unblocks_rules_4_and_7(self):
        s = settings()
        e = utc("2026-12-11T20:00:00Z")
        st = hb.status([], e + 3 * H + 60, s)
        assert st.pending and not st.in_slot and not st.blocks_runs

    def test_no_expiry_before_the_first_interval(self):
        st = hb.status([], utc("2026-11-30T00:00:00Z"), settings())
        assert st.expiry is None and not st.pending


class TestMissed:
    def test_with_the_trigger_in_act_it_is_missed_at_the_slot_end(self):
        s = settings()
        e = utc("2026-12-11T20:00:00Z")
        assert hb.missed([], e + 3 * H - 60, s, True) is None
        assert hb.missed([], e + 3 * H, s, True) == e

    def test_without_act_a_cycle_2_h_after_the_slot_end_is_no_miss(self):
        s = settings()
        e = utc("2026-12-11T20:00:00Z")
        late = hb.label([rec(e + 5 * H, 70.0)], s)
        assert late[0].kind == hb.BOOST
        assert hb.missed(late, e + 3 * H, s, False) is None
        assert hb.missed(late, e + 3 * H + 40 * H, s, False) is None

    def test_without_act_it_is_missed_after_the_wait(self):
        s = settings()
        e = utc("2026-12-11T20:00:00Z")
        assert hb.missed([], e + 3 * H + 36 * H, s, False) is None
        assert hb.missed([], e + 3 * H + 36 * H + 60, s, False) == e

    def test_the_next_expiry_follows_a_missed_one(self):
        s = settings()
        e = utc("2026-12-11T20:00:00Z")
        st = hb.status([], e + 3 * H + 36 * H + 60, s)
        assert not st.pending and st.next_expiry == e + 14 * DAY


def no_top(start, stop):
    return None


class TestIncomplete:
    def test_a_stall_at_50_c_is_incomplete_after_3_h(self):
        s = settings()
        e = utc("2026-12-11T20:00:00Z")
        labelled = hb.label([rec(e + 600, 50.0)], s)
        assert hb.incomplete(labelled, e + 600 + 3 * H - 60, no_top) == []
        assert hb.incomplete(labelled, e + 600 + 3 * H, no_top) == labelled
        assert hb.missed(labelled, e + 4 * H, s, True) is None

    def test_a_boost_that_reaches_62_c_is_complete(self):
        s = settings()
        e = utc("2026-12-11T20:00:00Z")
        labelled = hb.label([rec(e + 600, 69.0)], s)
        assert hb.incomplete(labelled, e + 5 * H, no_top) == []

    def test_the_tank_top_counts_when_the_cycle_peak_is_short(self):
        """A boost whose cycle record was cut short still heated the tank."""
        s = settings()
        e = utc("2026-12-11T20:00:00Z")
        labelled = hb.label([rec(e + 600, 50.0)], s)
        assert hb.incomplete(labelled, e + 5 * H, lambda a, b: 66.0) == []


class TestRetryInTheSlot:
    def test_a_retry_after_a_stall_is_the_boost_not_an_unexpected_one(self):
        s = settings()
        e = utc("2026-12-11T20:00:00Z")
        labelled = hb.label([rec(e + 600, 51.0), rec(e + 2 * H, 69.0)], s)
        assert [(r.kind, r.expiry, r.unexpected) for r in labelled] == [
            (hb.BOOST, e, False),
            (hb.BOOST, e, False),
        ]
        assert hb.status(labelled, e + 3 * H, s).boost.start == e + 2 * H
        fixed = hb.relabel_stalled(labelled, s)
        assert [(r.start, r.kind, r.expiry) for r in fixed] == [(e + 600, hb.REGULAR, e)]

    def test_a_hot_cycle_after_the_slot_is_unexpected(self):
        s = settings()
        e = utc("2026-12-11T20:00:00Z")
        labelled = hb.label([rec(e + 600, 51.0), rec(e + 4 * H, 69.0)], s)
        assert labelled[1].unexpected and labelled[1].expiry is None


class TestWrongAnchor:
    def test_the_stalled_cycle_is_relabelled_when_the_real_boost_comes(self):
        """A false expiry, a regular cycle, then a boost at 70 C 20 h later."""
        s = settings()
        e = utc("2026-12-11T20:00:00Z")
        labelled = hb.label([rec(e + H, 51.0), rec(e + 21 * H, 70.0)], s)
        assert labelled[0].kind == hb.BOOST and labelled[1].unexpected
        assert len(hb.incomplete(labelled, e + 4 * H + 60, no_top)) == 1
        fixed = hb.relabel_stalled(labelled, s)
        assert [(r.start, r.kind) for r in fixed] == [(e + H, hb.REGULAR)]

    def test_the_boost_stays_unexpected_after_the_relabel(self):
        """The relabelled cycle keeps its expiry, so the expiry stays taken."""
        s = settings()
        e = utc("2026-12-11T20:00:00Z")
        relabelled = hb.label([rec(e + 21 * H, 70.0)], s, frozenset({e}))
        assert relabelled[0].unexpected

    def test_no_relabel_without_an_unexpected_boost(self):
        s = settings()
        e = utc("2026-12-11T20:00:00Z")
        assert hb.relabel_stalled(hb.label([rec(e + H, 51.0)], s), s) == []


class TestSettingsAndFile:
    def test_a_missing_key_is_an_error(self):
        with pytest.raises(ValueError):
            hb.load_boost_settings({"anchor_utc": "2026-11-27T20:00:00Z"})

    def test_the_interval_must_exceed_the_slot_and_wait(self):
        with pytest.raises(ValueError):
            hb.load_boost_settings({**SECTION, "interval_days": 1})

    def test_a_time_without_a_zone_is_refused(self):
        with pytest.raises(ValueError):
            hb.parse_utc("2026-11-27T20:00:00")

    def test_the_anchor_file_round_trips_and_wins(self, tmp_path):
        path = tmp_path / "hot_water_boost.json"
        assert hb.read_anchor(path) is None
        hb.write_anchor(path, utc("2026-11-27T20:01:00Z"), utc("2026-11-28T08:00:00Z"))
        anchor = hb.read_anchor(path)
        assert anchor == utc("2026-11-27T20:01:00Z")
        assert hb.load_boost_settings(SECTION, anchor).anchor_utc == anchor

    def test_a_bad_anchor_file_is_an_error(self, tmp_path):
        path = tmp_path / "a.json"
        path.write_text("{}")
        with pytest.raises(ValueError):
            hb.read_anchor(path)
