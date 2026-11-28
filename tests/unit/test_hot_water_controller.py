"""The hot water tick with a fake outside world."""

import json
from pathlib import Path

import pytest
import yaml

from src.control import hot_water_boost as hb
from src.control import hot_water_controller as hwc
from src.control import hot_water_detect as hd
from src.control.hardware_implementations import MockHotWaterRelay
from tests.unit.test_hot_water_detect import frame, local

REPO = Path(__file__).parents[2]
SECTION = yaml.safe_load((REPO / "config" / "config.yaml").read_text(encoding="utf-8"))["hot_water"]
CUTOVER_MODES = {
    "relay_default": "act",
    "boost_trigger": "dry_run",
    "flush": "dry_run",
    "preheat": "dry_run",
    "pre_on": "dry_run",
    "comfort": "off",
}
H = 3600


def settings(modes=None, anchor=None) -> dict:
    section = dict(SECTION)
    if modes is not None:
        section["modes"] = {**CUTOVER_MODES, **modes}
    return hwc.load_settings(section, False, anchor)


class World:
    def __init__(self, minutes, history=None, clock=True, boot=0.0, entries=None):
        self.minutes = minutes
        self.history = [] if history is None else history
        self.points: list = []
        self.mails: list = []
        self.io = hwc.TickIO(
            read_minutes=lambda a, b: [m for m in self.minutes if a <= m.t <= b],
            read_history=lambda since: self.history,
            read_prices=lambda a, b: {},
            read_program=lambda now: [] if entries is None else entries,
            write=self.points.extend,
            mail=lambda subject, body: self.mails.append(subject) or True,
            clock_ok=lambda: clock,
            boot_time=lambda: boot,
        )

    def lines(self, measurement):
        return [p.to_line_protocol() for p in self.points if p._name == measurement]


def quiet(now, top=48.0, bottom=45.0, minutes=60):
    return [hd.Minute(now - k * 60, top, bottom, (0.8, 0.2, 0.2)) for k in range(minutes, -1, -1)]


def tick(world, now, tmp_path, relay=None, s=None):
    relay = MockHotWaterRelay("http://192.168.1.188/relay/0") if relay is None else relay
    result = hwc.run_once(now, settings() if s is None else s, relay, world.io, tmp_path)
    return result, relay


NOW = hb.parse_utc("2026-12-01T10:00:00Z")


class TestRequests:
    def test_a_cli_request_is_applied_by_the_next_tick(self, tmp_path):
        (tmp_path / hwc.REQUEST_FILE).write_text(json.dumps({"action": "circulate", "minutes": 30}))
        result, relay = tick(World(quiet(NOW)), NOW, tmp_path)
        assert result.request == "circulate 30 min"
        assert relay.commands == [("on", 180)]
        assert not (tmp_path / hwc.REQUEST_FILE).exists()

    def test_circulate_ends_and_rule_1_sets_off(self, tmp_path):
        (tmp_path / hwc.REQUEST_FILE).write_text(json.dumps({"action": "circulate", "minutes": 1}))
        world = World(quiet(NOW + 120))
        _, relay = tick(world, NOW, tmp_path)
        result, relay = tick(world, NOW + 120, tmp_path, relay)
        assert relay.commands[-1] == ("off", None)

    def test_a_bad_request_is_dropped(self, tmp_path):
        (tmp_path / hwc.REQUEST_FILE).write_text("{bad")
        result, relay = tick(World(quiet(NOW)), NOW, tmp_path)
        assert result.request == "" and relay.commands == []


class TestSafety:
    def _circulate(self, tmp_path):
        (tmp_path / hwc.REQUEST_FILE).write_text(json.dumps({"action": "circulate", "minutes": 30}))

    def test_no_on_before_the_clock_syncs(self, tmp_path):
        self._circulate(tmp_path)
        _, relay = tick(World(quiet(NOW), clock=False), NOW, tmp_path)
        assert relay.commands == []

    def test_stale_data_blocks_on(self, tmp_path):
        self._circulate(tmp_path)
        _, relay = tick(World(quiet(NOW - 600)), NOW, tmp_path)
        assert relay.commands == []

    def test_future_data_is_stale(self, tmp_path):
        self._circulate(tmp_path)
        minutes = quiet(NOW) + [hd.Minute(NOW + 600, 48.0, 45.0, (0.8, 0.2, 0.2))]
        result, relay = tick(World(minutes), NOW, tmp_path)
        assert relay.commands == [] and not result.inputs.tank.fresh

    def test_an_unreadable_history_blocks_on_and_writes_no_cycle(self, tmp_path):
        self._circulate(tmp_path)
        world = World(frame("regular"))
        world.io.read_history = lambda since: None
        now = world.minutes[-1].t
        _, relay = tick(world, now, tmp_path)
        assert relay.commands == [] and world.lines("hot_water_cycle") == []

    def test_relay_default_dry_run_never_switches(self, tmp_path):
        (tmp_path / hwc.REQUEST_FILE).write_text(json.dumps({"action": "circulate", "minutes": 5}))
        s = settings({"relay_default": "dry_run"})
        result, relay = tick(World(quiet(NOW)), NOW, tmp_path, s=s)
        assert relay.commands == [] and "would on" in result.action.reason


class TestCycleLog:
    def test_a_closed_cycle_is_written_once(self, tmp_path):
        world = World(frame("regular"))
        now = world.minutes[-1].t
        tick(world, now, tmp_path)
        lines = world.lines("hot_water_cycle")
        assert len(lines) == 1 and 'kind="regular"' in lines[0] and 'cause="pump"' in lines[0]
        written = {
            "start": local("2026-11-16 13:34"),
            "end": local("2026-11-16 13:50"),
            "kind": "regular",
            "expiry": 0,
            "unexpected": False,
            "start_top": 46.7,
            "peak_top": 50.7,
            "start_bottom": 45.1,
            "heater": False,
            "slow_start": False,
            "lost_on_min": 0,
        }
        world2 = World(frame("regular"), history=[written])
        tick(world2, now + 60, tmp_path)
        assert world2.lines("hot_water_cycle") == []

    def test_a_written_cycle_that_grows_again_is_the_open_cycle(self, tmp_path):
        minutes = [m for m in frame("regular") if m.t <= local("2026-11-16 13:44")]
        written = {
            "start": local("2026-11-16 13:34"),
            "end": local("2026-11-16 13:40"),
            "kind": "legionella_boost",
            "expiry": local("2026-11-16 13:00"),
            "unexpected": False,
            "start_top": 46.7,
            "peak_top": 47.0,
            "start_bottom": 45.1,
            "heater": False,
            "slow_start": False,
            "lost_on_min": 0,
        }
        world = World(minutes, history=[written])
        result, _ = tick(world, minutes[-1].t + 60, tmp_path)
        assert result.inputs.view.open is not None
        assert result.inputs.view.open.kind == "legionella_boost"
        assert world.lines("hot_water_cycle") == []

    def test_lost_minutes_in_program_on(self, tmp_path):
        entries = [(local("2026-11-16 12:00"), "ON")]
        world = World(frame("regular"), entries=entries)
        tick(world, world.minutes[-1].t, tmp_path)
        line = world.lines("hot_water_cycle")[0]
        lost = int(line.split("lost_on_min=")[1].split("i")[0])
        assert 0 < lost <= 20

    def test_the_tick_point_comes_on_change_and_every_15_min(self, tmp_path):
        world = World(quiet(NOW + 30 * 60, minutes=120))
        for k in range(0, 17):
            tick(world, NOW + k * 60, tmp_path)
        assert len(world.lines("hot_water")) == 2


class TestAlerts:
    def test_tank_under_the_floor(self, tmp_path):
        world = World(quiet(NOW, top=37.5, bottom=35.0))
        tick(world, NOW, tmp_path)
        assert any("floor" in m for m in world.mails)
        tick(world, NOW + 60, tmp_path)
        assert sum("floor" in m for m in world.mails) == 1

    def test_no_tank_data_for_15_min(self, tmp_path):
        world = World(quiet(NOW - 16 * 60))
        tick(world, NOW, tmp_path)
        assert any("no tank data" in m for m in world.mails)

    def test_one_failed_read_is_no_tank_data_mail(self, tmp_path):
        world = World(quiet(NOW))
        tick(world, NOW, tmp_path)
        world.minutes = []
        tick(world, NOW + 60, tmp_path)
        assert not any("no tank data" in m for m in world.mails)
        tick(world, NOW + 16 * 60, tmp_path)
        assert any("no tank data" in m for m in world.mails)

    def test_the_restart_mail_is_retried_until_sent(self, tmp_path):
        world = World(quiet(NOW + 120), boot=NOW - 300)
        world.io.mail = lambda subject, body: False
        tick(world, NOW, tmp_path)
        world.io.mail = lambda subject, body: world.mails.append(subject) or True
        tick(world, NOW + 60, tmp_path)
        assert sum("Pi restart" in m for m in world.mails) == 1
        tick(world, NOW + 120, tmp_path)
        assert sum("Pi restart" in m for m in world.mails) == 1

    def test_no_points_or_mails_before_the_clock_syncs(self, tmp_path):
        world = World(quiet(NOW, top=37.0), clock=False)
        tick(world, NOW, tmp_path)
        assert world.points == [] and world.mails == []

    def test_a_pi_restart_on_the_first_tick(self, tmp_path):
        world = World(quiet(NOW), boot=NOW - 300)
        tick(world, NOW, tmp_path)
        assert any("Pi restart" in m for m in world.mails)
        world.mails.clear()
        tick(world, NOW + 60, tmp_path)
        assert world.mails == []

    def test_start_level_drift(self, tmp_path):
        rows = [
            {
                "start": NOW - k * 6 * H,
                "end": NOW - k * 6 * H + 900,
                "kind": "regular",
                "expiry": 0,
                "unexpected": False,
                "start_top": 46.0,
                "peak_top": 51.0,
                "start_bottom": 44.5,
                "heater": False,
                "slow_start": True,
                "lost_on_min": 0,
            }
            for k in range(1, 6)
        ]
        world = World(quiet(NOW), history=rows)
        result, _ = tick(world, NOW, tmp_path)
        assert result.context.start_level == pytest.approx(44.5)
        assert any("drift" in m for m in world.mails)

    def _boost_row(self, start, peak, expiry):
        return {
            "start": start,
            "end": start + 1500,
            "kind": "legionella_boost",
            "expiry": expiry,
            "unexpected": False,
            "start_top": 46.0,
            "peak_top": peak,
            "start_bottom": 43.0,
            "heater": False,
            "slow_start": False,
            "lost_on_min": 0,
        }

    def test_a_stall_at_50_c_mails_incomplete_after_3_h_and_no_miss(self, tmp_path):
        s = settings({"boost_trigger": "act"}, anchor=hb.parse_utc("2026-11-17T08:00:00Z"))
        e = hb.parse_utc("2026-12-01T08:00:00Z")
        world = World(quiet(e + 3 * H + 660), history=[self._boost_row(e + 600, 50.0, e)])
        tick(world, e + 3 * H + 660, tmp_path, s=s)
        assert any("incomplete" in m for m in world.mails)
        assert not any("missed" in m for m in world.mails)

    def test_a_boost_that_reaches_62_c_mails_nothing(self, tmp_path):
        s = settings({"boost_trigger": "act"}, anchor=hb.parse_utc("2026-11-17T08:00:00Z"))
        e = hb.parse_utc("2026-12-01T08:00:00Z")
        world = World(quiet(e + 3 * H + 660), history=[self._boost_row(e + 600, 69.0, e)])
        tick(world, e + 3 * H + 660, tmp_path, s=s)
        assert not any("boost" in m for m in world.mails)

    def test_with_the_trigger_in_act_an_empty_slot_is_missed_at_its_end(self, tmp_path):
        s = settings({"boost_trigger": "act"}, anchor=hb.parse_utc("2026-11-17T08:00:00Z"))
        e = hb.parse_utc("2026-12-01T08:00:00Z")
        world = World(quiet(e + 3 * H))
        tick(world, e + 3 * H, tmp_path, s=s)
        assert any("missed" in m for m in world.mails)

    def test_in_dry_run_a_cycle_2_h_after_the_slot_is_no_miss(self, tmp_path):
        s = settings(anchor=hb.parse_utc("2026-11-17T08:00:00Z"))
        e = hb.parse_utc("2026-12-01T08:00:00Z")
        world = World(quiet(e + 6 * H), history=[self._boost_row(e + 5 * H, 69.0, e)])
        tick(world, e + 6 * H, tmp_path, s=s)
        assert not any("missed" in m for m in world.mails)

    def test_a_wrong_anchor_relabels_the_stalled_cycle(self, tmp_path):
        """A false expiry, a regular cycle, then a boost at 70 C 20 h later."""
        s = settings(anchor=hb.parse_utc("2026-11-17T08:00:00Z"))
        e = hb.parse_utc("2026-12-01T08:00:00Z")
        stalled = self._boost_row(e + H, 51.0, e)
        boost = {**self._boost_row(e + 21 * H, 70.0, 0), "unexpected": True}
        boost["kind"] = "legionella_boost"
        world = World(quiet(e + 21 * H + 1800), history=[stalled, boost])
        tick(world, e + 21 * H + 1800, tmp_path, s=s)
        relabel = [ln for ln in world.lines("hot_water_cycle") if "relabelled=true" in ln]
        assert len(relabel) == 1 and 'kind="regular"' in relabel[0]


class TestState:
    def test_round_trip(self, tmp_path):
        state = hwc.TickState(last_tick=5, sent={"a": 1.0}, sent_spans=[[1, 2, "x"]])
        state.rules.since["preheat"] = 3
        hwc.save_state(tmp_path / "s.json", state)
        loaded = hwc.load_state(tmp_path / "s.json")
        assert loaded.last_tick == 5 and loaded.rules.since == {"preheat": 3}
        assert loaded.sent_spans == [[1, 2, "x"]]

    def test_a_missing_state_is_a_first_tick(self, tmp_path):
        assert hwc.load_state(tmp_path / "none.json") is None

    def test_a_corrupt_state_starts_afresh(self, tmp_path):
        (tmp_path / "s.json").write_text("[1]")
        assert hwc.load_state(tmp_path / "s.json") == hwc.TickState()


class TestConfig:
    def test_config_yaml_modes_equal_the_cutover_table(self):
        assert SECTION["modes"] == CUTOVER_MODES
        assert SECTION["boost_budget"] is False and SECTION["planner_link"] is False

    def test_staging_modes_come_only_from_the_env_in_staging(self, monkeypatch):
        monkeypatch.setenv(hwc.STAGING_MODES_ENV, json.dumps({"boost_trigger": "act"}))
        assert hwc.load_settings(SECTION, False)["modes"]["boost_trigger"] == "dry_run"
        assert hwc.load_settings(SECTION, True)["modes"]["boost_trigger"] == "act"

    def test_bad_modes_are_refused(self):
        with pytest.raises(ValueError):
            hwc.load_settings({**SECTION, "modes": {**CUTOVER_MODES, "flush": "on"}}, False)
        with pytest.raises(ValueError):
            hwc.load_settings(
                {**SECTION, "modes": {**CUTOVER_MODES, "relay_default": "off"}}, False
            )

    def test_every_key_is_required(self):
        section = dict(SECTION)
        del section["floor_c"]
        with pytest.raises(ValueError):
            hwc.load_settings(section, False)

    def test_the_timer_must_cover_a_skipped_minute(self):
        with pytest.raises(ValueError):
            hwc.load_settings({**SECTION, "relay_on_timer_s": 100}, False)

    def test_both_tank_names_exist_in_the_sensor_data(self):
        """The names carry the umlauts the temperature collector writes."""
        dashboard = (REPO / "grafana" / "dashboards" / "production_dashboard.json").read_text(
            encoding="utf-8"
        )
        for key in ("tank_top_field", "tank_bottom_field"):
            assert f'"{SECTION[key]}"' in dashboard, SECTION[key]
            assert "ä" in SECTION[key]

    def test_the_relay_is_not_the_ventilation(self):
        assert "192.168.1.5/" not in SECTION["relay_url"]
