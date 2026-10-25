"""The real generator must make a sound program on and around every daylight
saving time change: the 25-hour autumn day repeats 03:00-03:59 and the 23-hour
spring day skips it. Only InfluxDB is replaced, by synthetic data."""

import datetime
import re
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import pytz
from freezegun import freeze_time

from src.common import config as app_config
from src.control import heating_data_fetcher as hdf
from src.control import program_generator as pg

HELSINKI = pytz.timezone("Europe/Helsinki")
DAYS = [
    "2026-10-24",  # its look-ahead reaches the 2026 autumn change
    "2026-10-25",  # 25 hours
    "2026-10-26",  # the first day after the autumn change
    "2027-03-27",
    "2027-03-28",  # 23 hours
    "2027-03-29",
    "2027-10-31",  # 25 hours
    "2027-11-01",
    "2026-11-10",  # an ordinary day
]
EVENING_SPIKE = (17, 18, 19)  # local hours priced above the EVU threshold


@pytest.fixture(autouse=True)
def _buckets(monkeypatch):
    """The bucket names the fetcher reads; a fresh config singleton per test."""
    for name in ("EMETERS", "SPOTPRICE", "WEATHER"):
        monkeypatch.setenv(f"INFLUXDB_BUCKET_{name}", name.lower())
    monkeypatch.setattr(app_config, "_config", None)


class _Record:
    def __init__(self, time, field, value):
        self._time, self._field, self._value = time, field, value

    def get_time(self):
        return self._time

    def get_field(self):
        return self._field

    def get_value(self):
        return self._value


class _Table:
    def __init__(self, records):
        self.records = records


def _fake_influx(spike):
    """Prices cheapest in the early morning, a flat 0 C forecast, no solar."""

    def query(flux):
        now = datetime.datetime.now(datetime.timezone.utc)
        start, stop = (int(n) for n in re.search(r"start: (-?\d+)d, stop: (-?\d+)d", flux).groups())
        times = pd.date_range(
            pd.Timestamp(now + datetime.timedelta(days=start)).ceil("15min"),
            pd.Timestamp(now + datetime.timedelta(days=stop)),
            freq="15min",
            inclusive="left",
        )
        records = []
        for t in times:
            hour = t.tz_convert(HELSINKI).hour
            when = t.to_pydatetime()
            if "price_total" in flux:
                price = (
                    0.50 if spike and hour in EVENING_SPIKE else 0.05 + 0.002 * ((hour + 20) % 24)
                )
                records += [_Record(when, "price_total", price), _Record(when, "price_sell", 0.03)]
            elif "Air temperature" in flux:
                records.append(_Record(when, "Air temperature", 0.0))
            else:
                records.append(_Record(when, "solar_yield_avg_prediction", 0.0))
        return [_Table(records)]

    influx = MagicMock()
    influx.query_with_retry.side_effect = query
    return influx


def _generate(day, spike):
    """Run the generator at 16:06 local on the eve of the day, as the timer does."""
    eve = HELSINKI.localize(
        datetime.datetime.combine(day - datetime.timedelta(days=1), datetime.time(16, 6))
    )
    offset_hours = int(eve.utcoffset().total_seconds() // 3600)
    influx = _fake_influx(spike)
    with (
        patch.object(pg, "InfluxClient", return_value=influx),
        patch.object(hdf, "InfluxClient", return_value=influx),
        freeze_time(eve.astimezone(pytz.utc).replace(tzinfo=None), tz_offset=offset_hours),
    ):
        return pg.HeatingProgramGenerator().generate_daily_program(date_offset=1)


@pytest.mark.parametrize("spike", [False, True], ids=["cheap-nights", "evening-spike"])
@pytest.mark.parametrize("day_text", DAYS)
def test_a_program_is_sound_on_and_around_dst_changes(day_text, spike):
    day = datetime.date.fromisoformat(day_text)
    program = _generate(day, spike)
    assert program["program_date"] == day_text

    hours = hdf.local_day_hours(day)
    schedule = program["loads"]["geothermal_pump"]["schedule"]
    stamps = [e["timestamp"] for e in schedule]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps), "one command per moment"
    assert all(hours[0] <= s < hours[-1] + 3600 for s in stamps), "inside the local day"
    for entry in schedule:
        label = datetime.datetime.fromtimestamp(entry["timestamp"], HELSINKI).isoformat()
        assert entry["local_time"] == label, entry

    on = [e for e in schedule if e["command"] == "ON"]
    assert on and all((e["timestamp"] - hours[0]) % 3600 == 0 for e in on), "whole hours"
    for entry in on + [e for e in schedule if e["command"] == "EVU"]:
        assert entry["timestamp"] + entry["duration_minutes"] * 60 in set(stamps), entry
    needed = program["planning_results"]["total_heating_hours_needed"]
    assert sum(e["duration_minutes"] for e in on) * 60 == round(needed * 3600)
    if spike:
        evu = [e for e in schedule if e["command"] == "EVU"]
        assert evu, "the evening spike gets an EVU block"
        spike_local = {datetime.datetime.fromtimestamp(e["timestamp"], HELSINKI).hour for e in evu}
        assert spike_local <= set(EVENING_SPIKE)
