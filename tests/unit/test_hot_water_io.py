"""The hot water tick's reads and points, with a fake InfluxDB and programs,
and the alert mailer."""

import datetime
import json
from unittest.mock import Mock

import pytest

from src.control import hot_water_boost as hb
from src.control import hot_water_io as hio
from src.monitoring import alert_mail

UTC = datetime.timezone.utc
T0 = 1_800_000_000


class Rec:
    def __init__(self, t, value=None, **values):
        self._t = datetime.datetime.fromtimestamp(t, UTC)
        self._value = value
        self.values = {"_time": self._t, "_value": value, **values}

    def get_time(self):
        return self._t

    def get_value(self):
        return self._value


class Table:
    def __init__(self, records):
        self.records = records


class Influx:
    def __init__(self, answers):
        self.answers = list(answers)
        self.queries: list = []

    def query_with_retry(self, flux):
        self.queries.append(flux)
        return self.answers.pop(0)


class Config:
    influxdb_bucket_temperatures = "temperatures"
    influxdb_bucket_shelly_em3_raw = "shelly_em3_emeters_raw"
    influxdb_bucket_spotprice = "spotprice"
    influxdb_bucket_load_control = "load_control"


SETTINGS = {"tank_top_field": "Käyttövesi ylh", "tank_bottom_field": "Käyttövesi alh"}


class TestReads:
    def test_minutes_join_tank_and_phases_in_kw(self):
        tank = [
            Table(
                [Rec(T0, **{SETTINGS["tank_top_field"]: 50.0, SETTINGS["tank_bottom_field"]: 45.0})]
            )
        ]
        power = [
            Table(
                [
                    Rec(T0, phase1_power=800.0, phase2_power=200.0, phase3_power=300.0),
                    Rec(T0 + 60, phase1_power=1.0),
                ]
            )
        ]
        influx = Influx([tank, power])
        minutes = hio.read_minutes(influx, Config(), SETTINGS, T0 - 60, T0 + 120)
        assert minutes[0].top == 50.0 and minutes[0].phases == pytest.approx((0.8, 0.2, 0.3))
        assert minutes[1].phases is None and minutes[1].top is None
        assert '"temperatures"' in influx.queries[0] and "pivot" in influx.queries[0]

    def test_prices_by_hour(self):
        influx = Influx([[Table([Rec(T0, 0.12), Rec(T0 + 3600, None)])]])
        assert hio.read_prices(influx, Config(), T0, T0 + 7200) == {T0: 0.12}

    def test_history_rows_carry_their_start(self):
        row = {k: 1 for k in hio.CYCLE_FIELDS}
        influx = Influx([[Table([Rec(T0, **row)])]])
        rows = hio.read_history(influx, Config(), T0 - 60, T0)
        assert rows[0]["start"] == T0 and "hot_water_cycle" in influx.queries[0]

    def test_a_history_row_without_its_fields_is_an_error(self):
        influx = Influx([[Table([Rec(T0, kind="regular")])]])
        with pytest.raises(ValueError):
            hio.read_history(influx, Config(), T0 - 60, T0)

    def test_to_record(self):
        row = {"start": T0, "end": T0 + 60, "start_top": -1, "peak_top": 51.0, "heater": False}
        rec = hio.to_record({**row, "kind": "regular", "expiry": 0, "unexpected": False})
        assert rec.expiry is None and rec.kind == hb.REGULAR and rec.start_top == -1.0


class TestProgram:
    def _program(self, tmp_path, day, entries):
        folder = tmp_path / day[:7]
        folder.mkdir(parents=True, exist_ok=True)
        program = {
            "program_date": day,
            "loads": {"geothermal_pump": {"schedule": entries, "power_kw": 3.0}},
        }
        (folder / f"heating_program_schedule_{day}.json").write_text(json.dumps(program))

    def test_entries_from_the_program_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            hio,
            "load_valid_program",
            lambda path: json.loads(path.read_text()) if path.exists() else None,
        )
        now = int(datetime.datetime(2026, 12, 2, 10, tzinfo=UTC).timestamp())
        self._program(tmp_path, "2026-12-02", [{"timestamp": now - 3600, "command": "ON"}])
        entries = hio.program_entries(now, str(tmp_path))
        assert entries == [(now - 3600, "ON")]

    def test_command_and_hour_maps(self):
        entries = [(0, "ALE"), (3600, "ON"), (7200, "EVU")]
        assert hio.command_at(entries, 3599) == "ALE" and hio.command_at(entries, -1) is None
        assert hio.hour_program(entries, 0, 3) == {0: "ALE", 3600: "ON", 7200: "EVU"}


class TestPoints:
    def test_cycle_point(self):
        rec = hb.Record(T0, T0 + 600, 46.0, 63.0, False, hb.BOOST, T0 - 60)
        line = hio.cycle_point(rec, None, 12, "boost_trigger").to_line_protocol()
        assert line.startswith("hot_water_cycle,data_type=actual ")
        assert 'kind="legionella_boost"' in line and "lost_on_min=12i" in line

    def test_relay_point(self):
        line = hio.relay_point(T0, True, "circulate", True).to_line_protocol()
        assert "load_id=hot_water_relay" in line and 'command="ON"' in line

    def test_tick_point(self):
        line = hio.tick_point(T0, {"relay_on": 1, "rule": "x"}).to_line_protocol()
        assert line.startswith("hot_water,data_type=actual ")


class TestMail:
    def _config(self, **values):
        config = Mock()
        config.get = lambda key: values[key] if key in values else None
        return config

    def test_staging_only_logs(self):
        assert alert_mail.mailer(self._config(), True)("s", "b")

    def test_without_settings_nothing_is_sent(self):
        assert not alert_mail.mailer(self._config(), False)("s", "b")

    def test_a_mail_is_sent(self, monkeypatch):
        sent = []
        monkeypatch.setattr(
            alert_mail, "send_alert_email", lambda *a, **k: sent.append((a, k)) or True
        )
        config = self._config(RESEND_API_KEY="k", ALERT_EMAIL_TO="to", ALERT_EMAIL_FROM="from")
        assert alert_mail.mailer(config, False)("subject", "body")
        assert sent[0][1] == {"from_email": "from"}

    def test_staging_by_bucket(self, monkeypatch):
        monkeypatch.delenv("STAGING_MODE", raising=False)
        assert alert_mail.is_staging(Mock(influxdb_bucket_load_control="load_control_staging"))
        assert not alert_mail.is_staging(Mock(influxdb_bucket_load_control="load_control"))
