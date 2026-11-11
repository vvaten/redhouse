"""The backfill prices each window as the bill does, and the old emeters
points come out in the emeters_5min units."""

import datetime
from unittest.mock import MagicMock, Mock

import pytest
import pytz

from deployment.backfill_aggregation import iter_windows
from deployment.backfill_bulk import (
    bulk_fetch_emeters,
    bulk_fetch_humidities,
    bulk_fetch_legacy_emeters,
    bulk_fetch_spotprices,
    bulk_fetch_weather,
    map_legacy_point,
    process_day_windows,
)
from src.aggregation import quarter_prices
from src.aggregation.analytics_1hour import Analytics1HourAggregator
from src.aggregation.analytics_15min import Analytics15MinAggregator


def _at(hour, minute=0):
    return datetime.datetime(2026, 1, 8, hour, minute, tzinfo=pytz.UTC)


def _spot(hour, minute, price):
    return {
        "time": _at(hour, minute),
        "price_total": price,
        "price_sell": 0.02,
        "price_withtax": None,
    }


def _energy(hour, minute):
    return {
        "time": _at(hour, minute),
        "consumption_avg": 1200.0,
        "consumption_diff": 100.0,
        "solar_yield_avg": 0.0,
        "emeter_avg": 1200.0,
    }


def _run(aggregator, minutes, spots):
    emeters = [_energy(10, 5 * i) for i in range(12)]
    _points, ok, skipped = process_day_windows(
        aggregator, emeters, spots, {}, {}, {}, _at(10), _at(11), minutes, iter_windows, False
    )
    return ok, skipped


def _prices_seen(aggregator, minutes, spots):
    seen = []
    calculate = aggregator.calculate_metrics

    def spy(raw_data, start, end):
        metrics = calculate(raw_data, start, end)
        seen.append((start, metrics["price_total"], metrics["grid_import_cost"]))
        return metrics

    aggregator.calculate_metrics = spy
    assert _run(aggregator, minutes, spots) == (60 // minutes, 0)
    return seen


QUARTER_SPOTS = [_spot(9, 45, 0.90)] + [_spot(10, 15 * i, 0.1 * (i + 1)) for i in range(4)]
QUARTER_SPOTS.append(_spot(11, 0, 0.80))


def test_each_15min_window_takes_the_bill_price_of_its_hour():
    seen = _prices_seen(Analytics15MinAggregator(MagicMock(), MagicMock()), 15, QUARTER_SPOTS)
    assert [(s, p) for s, p, _ in seen] == [
        (_at(10, 15 * i), pytest.approx(0.25)) for i in range(4)
    ]


def test_an_hourly_window_costs_the_hour_at_its_bill_price():
    seen = _prices_seen(Analytics1HourAggregator(MagicMock(), MagicMock()), 60, QUARTER_SPOTS)
    ((start, price, cost),) = seen
    assert start == _at(10)
    assert price == pytest.approx(0.25)
    assert cost == pytest.approx(1.2 * 0.25)


def test_with_quarter_netting_each_15min_window_takes_its_own_price(monkeypatch):
    monkeypatch.setattr(quarter_prices, "QUARTER_NETTING_FROM", _at(0))
    seen = _prices_seen(Analytics15MinAggregator(MagicMock(), MagicMock()), 15, QUARTER_SPOTS)
    assert [p for _, p, _ in seen] == [pytest.approx(0.1 * (i + 1)) for i in range(4)]


def test_an_hourly_price_covers_its_hour():
    hour = datetime.datetime(2025, 1, 8, 10, tzinfo=pytz.UTC)
    spots = [{"time": hour, "price_total": 0.30, "price_sell": 0.02, "price_withtax": None}]
    emeters = [
        {"time": hour + datetime.timedelta(minutes=5 * i), "consumption_diff": 100.0}
        for i in range(12)
    ]
    seen = []
    aggregator = Mock()
    aggregator.calculate_metrics.side_effect = lambda raw, s, e: seen.extend(raw["spotprice"]) or {
        "net_cost": 0.0
    }

    process_day_windows(
        aggregator,
        emeters,
        spots,
        {},
        {},
        {},
        hour,
        hour + datetime.timedelta(hours=1),
        15,
        iter_windows,
        False,
    )

    assert [q["price_total"] for q in seen] == [0.30] * 4


def test_an_old_point_is_converted_from_wh_per_second_to_watts():
    values = {
        "_time": _at(10),
        "consumption_avg": 1.476,
        "consumption_diff": 442.9,
        "emeter_avg": 1.476,
        "emeter_diff": 442.9,
        "solar_yield_avg": 0.0,
        "solar_yield_diff": 0.0,
        "battery_charge_avg": 0.1,
        "ts_diff": 300,
    }
    point = map_legacy_point(values, {"Battery_SoC": 55.0})
    assert point["time"] == _at(10)
    assert point["consumption_avg"] == pytest.approx(5313.6)
    assert point["consumption_diff"] == 442.9
    assert point["battery_charge_diff"] == pytest.approx(30.0)
    assert point["battery_discharge_avg"] is None and point["battery_discharge_diff"] is None
    assert point["Battery_SoC"] == 55.0


# An old point from before CheckWatt: net meter power and solar only
BEFORE_CHECKWATT = {
    "_time": _at(10),
    "consumption_avg": 0.5,
    "consumption_diff": 150.0,
    "emeter_avg": -0.25,
    "emeter_diff": -75.0,
    "solar_yield_avg": 0.75,
    "solar_yield_diff": 225.0,
    "ts_diff": 300,
}


def test_before_checkwatt_import_and_export_are_the_signs_of_the_meter():
    exporting = map_legacy_point(BEFORE_CHECKWATT, {})
    importing = map_legacy_point({**BEFORE_CHECKWATT, "emeter_avg": 0.5}, {})
    assert (exporting["energy_import_avg"], exporting["energy_export_avg"]) == (0.0, 900.0)
    assert (importing["energy_import_avg"], importing["energy_export_avg"]) == (1800.0, 0.0)


def test_checkwatt_fills_a_point_without_battery_fields():
    checkwatt = {
        "Battery_SoC": 50.0,
        "SolarYield": 600.0,
        "BatteryCharge": 1200.0,
        "BatteryDischarge": 0.0,
        "EnergyImport": 1500.0,
        "EnergyExport": 0.0,
    }
    point = map_legacy_point(
        {**BEFORE_CHECKWATT, "emeter_avg": 0.5, "emeter_diff": 150.0}, checkwatt
    )
    assert point["battery_charge_avg"] == 1200.0
    assert point["battery_charge_diff"] == pytest.approx(100.0)
    assert point["solar_yield_avg"] == 600.0
    # CheckWatt's grid fields read 0 then, so the meter gives the grid import
    assert (point["energy_import_avg"], point["energy_export_avg"]) == (1800.0, 0.0)
    # Meter 1800 W + solar 600 W - charge 1200 W
    assert point["consumption_avg"] == pytest.approx(1200.0)
    assert point["consumption_diff"] == pytest.approx(150.0 + 50.0 - 100.0)


def test_the_old_points_get_the_checkwatt_values_of_their_own_5min_window():
    config = Mock(influxdb_bucket_emeters="emeters", influxdb_bucket_checkwatt="checkwatt")

    def record(time, values=None, field=None, value=None):
        r = Mock(values={"_time": time, **(values or {})})
        r.get_time.return_value = time
        r.get_field.return_value = field
        r.get_value.return_value = value
        return r

    socs = [Mock(records=[record(_at(10, 5), field="Battery_SoC", value=61.0)])]
    power = [Mock(records=[record(_at(10, 5), field="BatteryCharge", value=None)])]
    used = {"consumption_avg": 1.0, "consumption_diff": 300.0}
    energy = [
        Mock(
            records=[
                record(_at(10, 5), used),
                record(_at(10), {"consumption_diff": 1.0}),
                record(_at(10, 10), {"emeter_diff": 150.0}),
            ]
        )
    ]
    client = Mock()
    client.query_with_retry.side_effect = [socs, power, energy]

    points = bulk_fetch_legacy_emeters(client, config, _at(10), _at(11))

    # The point without consumption has no CheckWatt data and is left out
    assert [(p["time"], p["Battery_SoC"]) for p in points] == [(_at(10), None), (_at(10, 5), 61.0)]
    assert points[1]["consumption_avg"] == pytest.approx(3600.0)


def test_the_weather_fields_are_read_by_their_fmi_names():
    record = Mock()
    record.get_time.return_value = _at(10)
    record.get_field.return_value = "Air temperature"
    record.get_value.return_value = -3.5
    client = Mock()
    client.query_with_retry.return_value = [Mock(records=[record])]
    config = Mock(influxdb_bucket_weather="weather")

    weather = bulk_fetch_weather(client, config, _at(10), _at(11), 60)

    assert 'r._field == "Total cloud cover"' in client.query_with_retry.call_args.args[0]
    assert weather == {_at(10): {"air_temperature": -3.5}}


def test_a_range_ending_inside_an_hour_reads_the_prices_of_the_whole_hour():
    client = Mock()
    client.query_with_retry.return_value = []
    config = Mock(influxdb_bucket_spotprice="spotprice")
    config.spot_prices_config = {"production_buyback_margin": 0.30}

    bulk_fetch_spotprices(client, config, _at(0), _at(10, 37))

    query = client.query_with_retry.call_args.args[0]
    assert f"start: {_at(0).replace(day=7, hour=23).isoformat()}" in query
    assert f"stop: {_at(11).isoformat()}" in query


def test_a_price_without_a_sell_price_gets_one_from_the_buyback_margin():
    def record(time, values):
        r = Mock(values=values)
        r.get_time.return_value = time
        return r

    old = {"price": 0.05, "price_total": 0.12, "price_withtax": 0.06}
    new = {**old, "price_sell": 0.04}
    client = Mock()
    client.query_with_retry.return_value = [
        Mock(records=[record(_at(11), new), record(_at(10), old)])
    ]
    config = Mock(influxdb_bucket_spotprice="spotprice")
    config.spot_prices_config = {"production_buyback_margin": 0.30}

    points = bulk_fetch_spotprices(client, config, _at(10), _at(12))

    assert [p["price_sell"] for p in points] == [pytest.approx(0.047), 0.04]


def test_influxdb_takes_the_sensor_means_per_window_and_each_window_gets_its_own():
    def mean(time, field, value):
        r = Mock()
        r.get_time.return_value = time
        r.get_field.return_value = field
        r.get_value.return_value = value
        return r

    client = Mock()
    client.query_with_retry.return_value = [
        Mock(records=[mean(_at(10), "Hilla", 40.0), mean(_at(10, 15), "Hilla", 42.0)])
    ]
    config = Mock(influxdb_bucket_temperatures="temperatures")

    humidities = bulk_fetch_humidities(client, config, _at(10), _at(11), 15)

    query = client.query_with_retry.call_args.args[0]
    assert 'aggregateWindow(every: 15m, fn: mean, timeSrc: "_start"' in query
    assert humidities == {_at(10): {"hum_Hilla": 40.0}, _at(10, 15): {"hum_Hilla": 42.0}}

    seen = {}
    aggregator = Mock()
    aggregator.calculate_metrics.side_effect = lambda raw, s, e: seen.update(
        {s: raw["humidities"]}
    ) or {"net_cost": 0.0}
    emeters = [_energy(10, 5 * i) for i in range(6)]
    process_day_windows(
        aggregator, emeters, [], {}, {}, humidities, _at(10), _at(10, 30), 15, iter_windows, False
    )
    assert seen == {_at(10): {"hum_Hilla": 40.0}, _at(10, 15): {"hum_Hilla": 42.0}}


def test_a_window_with_points_missing_consumption_is_reported_as_partial(capsys):
    emeters = [_energy(10, 5 * i) for i in range(12)]
    for point in emeters[7:]:
        point["consumption_diff"] = None
    aggregator = Mock()
    aggregator.calculate_metrics.return_value = {"net_cost": 0.0}

    process_day_windows(
        aggregator, emeters, [], {}, {}, {}, _at(10), _at(11), 60, iter_windows, False
    )

    assert f"PARTIAL {_at(10).isoformat()} 7/12" in capsys.readouterr().out


def test_an_emeters_5min_row_has_every_field_even_when_the_day_lacks_one():
    record = Mock(values={"emeter_avg": 1000.0})
    record.get_time.return_value = _at(10)
    client = Mock()
    client.query_with_retry.return_value = [Mock(records=[record])]
    config = Mock(influxdb_bucket_emeters_5min="emeters_5min")

    (point,) = bulk_fetch_emeters(client, config, _at(10), _at(11))

    assert point["emeter_avg"] == 1000.0
    assert point["consumption_avg"] is None and point["solar_yield_avg"] is None


def test_the_gap_fill_needs_both_meter_values_and_drops_unreasonable_power():
    checkwatt = {
        "Battery_SoC": 50.0,
        "SolarYield": 99999.0,
        "BatteryCharge": 1200.0,
        "BatteryDischarge": 0.0,
    }
    without_diff = map_legacy_point({**BEFORE_CHECKWATT, "emeter_diff": None}, checkwatt)
    assert without_diff["battery_charge_avg"] is None
    assert without_diff["consumption_avg"] == pytest.approx(1800.0)

    point = map_legacy_point(BEFORE_CHECKWATT, checkwatt)
    assert point["solar_yield_avg"] == 0.0 and point["battery_charge_avg"] == 1200.0
