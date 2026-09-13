"""Tests that production alert rules follow the running timers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "deployment"))

import setup_grafana_alerts as sga  # noqa: E402
from alert_rules import ALERT_RULES  # noqa: E402


def rule(name):
    for definition in ALERT_RULES:
        if definition["name"] == name:
            return definition
    raise AssertionError("no rule named %r" % name)


class TestEveryRuleNamesItsTimer:
    def test_all_rules_tagged(self):
        """An untagged rule would silently bypass the timer filter."""
        untagged = [r["name"] for r in ALERT_RULES if not r.get("timer")]
        assert untagged == []

    def test_timer_names_look_like_units(self):
        for definition in ALERT_RULES:
            assert definition["timer"].startswith("redhouse-")
            assert not definition["timer"].endswith(".timer")


class TestRuleIsWanted:
    RUNNING = {"redhouse-temperature", "redhouse-checkwatt"}

    def test_running_collector_is_wanted(self):
        wanted, _ = sga.rule_is_wanted(rule("Temperature data stale"), "production", self.RUNNING)
        assert wanted is True

    def test_stopped_collector_is_skipped(self):
        wanted, reason = sga.rule_is_wanted(
            rule("5min aggregation stale"), "production", self.RUNNING
        )
        assert wanted is False
        assert "not running" in reason

    def test_skip_envs_still_honoured(self):
        wanted, reason = sga.rule_is_wanted(
            rule("Temperature data stale"), "staging", {"redhouse-temperature"}
        )
        assert wanted is False
        assert "not applicable" in reason

    def test_no_filter_when_running_is_unknown(self):
        """wibatemp and staging are not filtered by redhouse timers."""
        wanted, _ = sga.rule_is_wanted(rule("Weather data stale"), "wibatemp", None)
        assert wanted is True

    def test_todays_production_state_yields_two_rules(self):
        """Only temperature and checkwatt run, so only those two alert."""
        kept = [
            r["name"] for r in ALERT_RULES if sga.rule_is_wanted(r, "production", self.RUNNING)[0]
        ]
        assert kept == ["Temperature data stale", "CheckWatt data stale"]

    def test_all_rules_return_once_every_timer_runs(self):
        every = {r["timer"] for r in ALERT_RULES}
        kept = [r["name"] for r in ALERT_RULES if sga.rule_is_wanted(r, "production", every)[0]]
        assert len(kept) == len(ALERT_RULES)


class TestActiveProductionTimers:
    def test_returns_none_without_systemctl(self, monkeypatch):
        def boom(*args, **kwargs):
            raise FileNotFoundError("systemctl")

        monkeypatch.setattr(sga.subprocess, "run", boom)
        assert sga.active_production_timers() is None

    def test_parses_unit_names_and_drops_staging(self, monkeypatch):
        class Result:
            returncode = 0
            stdout = (
                "redhouse-temperature.timer loaded active waiting Temp\n"
                "redhouse-checkwatt.timer loaded active waiting CheckWatt\n"
                "redhouse-staging-weather.timer loaded active waiting Staging\n"
            )

        monkeypatch.setattr(sga.subprocess, "run", lambda *a, **k: Result())
        assert sga.active_production_timers() == {
            "redhouse-temperature",
            "redhouse-checkwatt",
        }

    def test_returns_none_on_nonzero_exit(self, monkeypatch):
        class Result:
            returncode = 1
            stdout = ""

        monkeypatch.setattr(sga.subprocess, "run", lambda *a, **k: Result())
        assert sga.active_production_timers() is None
