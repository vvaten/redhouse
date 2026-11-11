"""Unit tests for analytics base class."""

import datetime
from unittest.mock import MagicMock, Mock

import pytest
import pytz

from src.aggregation import quarter_prices as quarter_prices_module
from src.aggregation.analytics_base import AnalyticsAggregatorBase
from src.aggregation.quarter_prices import bill_periods, quarter_prices
from src.common.influx_client import InfluxClient


class ConcreteAnalyticsAggregator(AnalyticsAggregatorBase):
    """Concrete implementation for testing the base class."""

    INTERVAL_SECONDS = 900  # 15 minutes

    def calculate_metrics(self, raw_data, window_start, window_end):
        """Dummy implementation."""
        return {"test": "metrics"}

    def write_results(self, metrics, timestamp):
        """Dummy implementation."""
        return True


@pytest.fixture
def mock_influx_client():
    """Create a mock InfluxDB client."""
    client = MagicMock(spec=InfluxClient)
    client.query_api = MagicMock()
    client.write_api = MagicMock()
    return client


@pytest.fixture
def config():
    """Get mock configuration."""
    mock_config = MagicMock()
    mock_config.influxdb_org = "test_org"
    mock_config.influxdb_bucket_emeters_5min = "test_emeters_5min"
    mock_config.influxdb_bucket_spotprice = "test_spotprice"
    mock_config.influxdb_bucket_weather = "test_weather"
    mock_config.influxdb_bucket_temperatures = "test_temperatures"
    mock_config.influxdb_bucket_analytics_15min = "test_analytics_15min"
    mock_config.influxdb_bucket_analytics_1hour = "test_analytics_1hour"
    return mock_config


@pytest.fixture
def aggregator(mock_influx_client, config):
    """Create a concrete analytics aggregator instance."""
    return ConcreteAnalyticsAggregator(mock_influx_client, config)


@pytest.fixture
def time_window():
    """Create a test time window."""
    tz = pytz.timezone("Europe/Helsinki")
    window_start = tz.localize(datetime.datetime(2026, 1, 8, 10, 0, 0))
    window_end = tz.localize(datetime.datetime(2026, 1, 8, 10, 15, 0))
    return window_start, window_end


class TestAnalyticsAggregatorBase:
    """Test the AnalyticsAggregatorBase class."""

    def test_initialization(self, aggregator, mock_influx_client, config):
        """Test that aggregator is initialized correctly."""
        assert aggregator.influx == mock_influx_client
        assert aggregator.config == config
        assert aggregator.INTERVAL_SECONDS == 900

    def test_fetch_emeters_5min_data_success(self, aggregator, time_window):
        """Test successful fetch of emeters_5min data."""
        window_start, window_end = time_window

        # Mock InfluxDB response
        mock_record = Mock()
        mock_record.get_time.return_value = window_start
        mock_record.values = {
            "solar_yield_avg": 2000.0,
            "solar_yield_diff": 166.67,
            "consumption_avg": 3000.0,
            "consumption_diff": 250.0,
            "emeter_avg": 1000.0,
            "emeter_diff": 83.33,
            "battery_charge_avg": 0.0,
            "battery_charge_diff": 0.0,
            "battery_discharge_avg": 0.0,
            "battery_discharge_diff": 0.0,
            "Battery_SoC": 65.0,
            "energy_import_avg": 1000.0,
            "energy_export_avg": 0.0,
        }

        mock_table = Mock()
        mock_table.records = [mock_record]

        aggregator.influx.query_with_retry.return_value = [mock_table]

        data = aggregator._fetch_emeters_5min_data(window_start, window_end)

        assert len(data) == 1
        assert data[0]["solar_yield_avg"] == 2000.0
        assert data[0]["consumption_avg"] == 3000.0
        assert data[0]["Battery_SoC"] == 65.0

    def test_fetch_emeters_5min_data_empty(self, aggregator, time_window):
        """Test fetch of emeters_5min data with no results."""
        window_start, window_end = time_window
        aggregator.influx.query_with_retry.return_value = []

        data = aggregator._fetch_emeters_5min_data(window_start, window_end)

        assert data == []

    def test_fetch_emeters_5min_data_exception(self, aggregator, time_window):
        """A failed read must raise, not look like an empty window.

        emeters_5min is the required input. Returning [] here made
        validate_data reject the window as invalid, so a transient read
        error turned into permanently missing analytics.
        """
        window_start, window_end = time_window
        aggregator.influx.query_with_retry.side_effect = Exception("Database error")

        with pytest.raises(Exception, match="Database error"):
            aggregator._fetch_emeters_5min_data(window_start, window_end)

    def test_empty_window_and_failed_read_are_distinguishable(self, aggregator, time_window):
        """The two cases must not produce the same result."""
        window_start, window_end = time_window

        aggregator.influx.query_with_retry.return_value = []
        assert aggregator._fetch_emeters_5min_data(window_start, window_end) == []

        aggregator.influx.query_with_retry.side_effect = Exception("Read timed out")
        with pytest.raises(Exception, match="Read timed out"):
            aggregator._fetch_emeters_5min_data(window_start, window_end)

    def test_a_quarter_window_reads_its_whole_hour_for_the_bill_price(
        self, aggregator, time_window
    ):
        window_start, window_end = time_window
        records = []
        for minutes, price in ((-15, 9.0), (0, 8.0), (15, 7.0), (30, 6.0), (45, 5.0)):
            record = Mock()
            record.get_time.return_value = window_start + datetime.timedelta(minutes=minutes)
            record.values = {"price_total": price, "price_sell": 4.0, "price_withtax": None}
            records.append(record)
        aggregator.influx.query_with_retry.return_value = [Mock(records=records)]

        periods = aggregator._fetch_spotprice_data(window_start, window_end)

        assert periods == [
            {
                "start": window_start,
                "stop": window_end,
                "price_total": 6.5,
                "price_sell": 4.0,
                "price_withtax": None,
            }
        ]
        query = aggregator.influx.query_with_retry.call_args.args[0]
        assert f"start: {(window_start - datetime.timedelta(hours=1)).isoformat()}" in query
        assert f"stop: {(window_start + datetime.timedelta(hours=1)).isoformat()}" in query

    def test_no_price_points_give_a_period_without_prices(self, aggregator, time_window):
        window_start, window_end = time_window
        aggregator.influx.query_with_retry.return_value = []

        periods = aggregator._fetch_spotprice_data(window_start, window_end)

        assert periods == [
            {
                "start": window_start,
                "stop": window_end,
                "price_total": None,
                "price_sell": None,
                "price_withtax": None,
            }
        ]

    def test_a_failed_price_read_raises_so_the_window_is_retried(self, aggregator, time_window):
        window_start, window_end = time_window
        aggregator.influx.query_with_retry.side_effect = Exception("Database error")

        with pytest.raises(Exception, match="Database error"):
            aggregator._fetch_spotprice_data(window_start, window_end)

    def test_fetch_weather_data_success(self, aggregator, time_window):
        """Test successful fetch of weather data."""
        window_start, window_end = time_window

        # Mock InfluxDB response with multiple records
        mock_records = []
        for field_name, value in [
            ("Air temperature", 5.5),
            ("Total cloud cover", 50.0),
            ("Global radiation", 150.0),
            ("Wind speed", 4.5),
        ]:
            mock_record = Mock()
            mock_record.get_field.return_value = field_name
            mock_record.get_value.return_value = value
            mock_records.append(mock_record)

        mock_table = Mock()
        mock_table.records = mock_records

        aggregator.influx.query_with_retry.return_value = [mock_table]

        weather = aggregator._fetch_weather_data(window_start, window_end)

        assert weather == {
            "air_temperature": 5.5,
            "cloud_cover": 50.0,
            "solar_radiation": 150.0,
            "wind_speed": 4.5,
        }
        query = aggregator.influx.query_with_retry.call_args.args[0]
        assert 'r._field == "Air temperature"' in query
        assert 'r._field == "Wind speed"' in query

    def test_fetch_weather_data_empty(self, aggregator, time_window):
        """Test fetch of weather data with no results."""
        window_start, window_end = time_window
        aggregator.influx.query_with_retry.return_value = []

        weather = aggregator._fetch_weather_data(window_start, window_end)

        assert weather is None

    def test_fetch_weather_data_exception(self, aggregator, time_window):
        """Test fetch of weather data with exception."""
        window_start, window_end = time_window
        aggregator.influx.query_with_retry.side_effect = Exception("Database error")

        weather = aggregator._fetch_weather_data(window_start, window_end)

        assert weather is None

    def test_fetch_temperatures_data_success(self, aggregator, time_window):
        """Test successful fetch of temperature data."""
        window_start, window_end = time_window

        # Mock InfluxDB response with multiple records
        mock_records = []
        for field_name, value in [("PaaMH", 50.0), ("Ulkolampo", 5.0), ("PalMH", 40.0)]:
            mock_record = Mock()
            mock_record.get_field.return_value = field_name
            mock_record.get_value.return_value = value
            mock_records.append(mock_record)

        mock_table = Mock()
        mock_table.records = mock_records

        aggregator.influx.query_with_retry.return_value = [mock_table]

        temperatures = aggregator._fetch_temperatures_data(window_start, window_end)

        assert temperatures is not None
        assert temperatures["PaaMH"] == 50.0
        assert temperatures["Ulkolampo"] == 5.0
        assert temperatures["PalMH"] == 40.0

    def test_fetch_temperatures_data_empty(self, aggregator, time_window):
        """Test fetch of temperature data with no results."""
        window_start, window_end = time_window
        aggregator.influx.query_with_retry.return_value = []

        temperatures = aggregator._fetch_temperatures_data(window_start, window_end)

        assert temperatures is None

    def test_fetch_temperatures_data_exception(self, aggregator, time_window):
        """Test fetch of temperature data with exception."""
        window_start, window_end = time_window
        aggregator.influx.query_with_retry.side_effect = Exception("Database error")

        temperatures = aggregator._fetch_temperatures_data(window_start, window_end)

        assert temperatures is None

    def test_fetch_data_orchestration(self, aggregator, time_window):
        """Test fetch_data orchestrates all fetch methods."""
        window_start, window_end = time_window

        # Mock all fetch methods
        aggregator._fetch_emeters_5min_data = MagicMock(return_value=[{"test": "emeters"}])
        aggregator._fetch_spotprice_data = MagicMock(return_value={"test": "spotprice"})
        aggregator._fetch_weather_data = MagicMock(return_value={"test": "weather"})
        aggregator._fetch_temperatures_data = MagicMock(return_value={"test": "temperatures"})
        aggregator._fetch_humidities_data = MagicMock(return_value={"test": "humidities"})

        raw_data = aggregator.fetch_data(window_start, window_end)

        assert raw_data["emeters"] == [{"test": "emeters"}]
        assert raw_data["spotprice"] == {"test": "spotprice"}
        assert raw_data["weather"] == {"test": "weather"}
        assert raw_data["temperatures"] == {"test": "temperatures"}
        assert raw_data["humidities"] == {"test": "humidities"}

        aggregator._fetch_emeters_5min_data.assert_called_once_with(window_start, window_end)
        aggregator._fetch_spotprice_data.assert_called_once_with(window_start, window_end)
        aggregator._fetch_weather_data.assert_called_once_with(window_start, window_end)
        aggregator._fetch_temperatures_data.assert_called_once_with(window_start, window_end)
        aggregator._fetch_humidities_data.assert_called_once_with(window_start, window_end)

    def test_validate_data_with_emeters(self, aggregator):
        """Test validation with emeters data."""
        raw_data = {"emeters": [{"test": "data"}]}
        assert aggregator.validate_data(raw_data) is True

    def test_validate_data_without_emeters(self, aggregator):
        """Test validation without emeters data."""
        raw_data = {"emeters": []}
        assert aggregator.validate_data(raw_data) is False

    def test_validate_data_missing_key(self, aggregator):
        """Test validation with missing emeters key."""
        raw_data = {}
        assert aggregator.validate_data(raw_data) is False

    def test_calculate_cost_allocation(self, aggregator):
        """Test cost allocation calculation."""
        metrics = {
            "solar_yield_sum": 2000.0,
            "consumption_sum": 3000.0,
            "battery_charge_sum": 0.0,
            "battery_discharge_sum": 0.0,
            "export_sum": 0.0,
        }
        spotprice = {"price_total": 0.085, "price_sell": 0.04}

        cost_metrics = aggregator._calculate_cost_allocation(metrics, spotprice)

        assert "solar_to_consumption" in cost_metrics
        assert "solar_direct_value" in cost_metrics
        assert cost_metrics["solar_to_consumption"] == 2000.0
        assert cost_metrics["solar_direct_value"] == pytest.approx(0.17, rel=0.01)

    def test_calculate_cost_allocation_missing_prices(self, aggregator):
        """Test cost allocation with missing price data."""
        metrics = {"solar_yield_sum": 2000.0, "consumption_sum": 3000.0}
        spotprice = {"price_total": None, "price_sell": 4.0}

        cost_metrics = aggregator._calculate_cost_allocation(metrics, spotprice)

        assert cost_metrics == {}

    def test_calculate_self_consumption(self, aggregator):
        """Test self-consumption calculation."""
        metrics = {
            "solar_yield_sum": 2000.0,
            "battery_charge_sum": 0.0,
            "export_sum": 500.0,
            "solar_to_consumption": 1500.0,
        }

        self_consumption = aggregator._calculate_self_consumption(metrics)

        assert self_consumption["solar_direct_sum"] == 1500.0
        assert self_consumption["self_consumption_ratio"] == pytest.approx(75.0, rel=0.01)

    def test_calculate_self_consumption_no_solar(self, aggregator):
        """Test self-consumption with no solar production."""
        metrics = {"solar_yield_sum": 0.0, "battery_charge_sum": 0.0, "export_sum": 0.0}

        self_consumption = aggregator._calculate_self_consumption(metrics)

        assert self_consumption["solar_direct_sum"] == 0.0
        assert self_consumption["self_consumption_ratio"] == 0.0

    def test_add_weather_and_temperature_fields(self, aggregator):
        """Test adding weather and temperature fields."""
        metrics = {}
        weather = {
            "air_temperature": 5.5,
            "cloud_cover": 50.0,
            "solar_radiation": 150.0,
            "wind_speed": 4.5,
        }
        temperatures = {"PaaMH": 50.0, "Ulkolampo": 5.0}

        aggregator._add_weather_and_temperature_fields(metrics, weather, temperatures)

        assert metrics["air_temperature"] == 5.5
        assert metrics["cloud_cover"] == 50.0
        assert metrics["PaaMH"] == 50.0
        assert metrics["Ulkolampo"] == 5.0

    def test_add_weather_and_temperature_fields_none(self, aggregator):
        """Test adding weather and temperature fields with None data."""
        metrics = {}

        aggregator._add_weather_and_temperature_fields(metrics, None, None)

        assert "air_temperature" not in metrics
        assert "PaaMH" not in metrics

    def test_soc_is_the_last_known_value_and_absent_when_unknown(self, aggregator):
        known = aggregator._calculate_energy_metrics([{"Battery_SoC": 40.0}, {"Battery_SoC": None}])
        unknown = aggregator._calculate_energy_metrics([{"Battery_SoC": None}])
        assert known["Battery_SoC"] == 40.0
        assert unknown["Battery_SoC"] is None


def _at(hour, minute=0, year=2026):
    return datetime.datetime(year, 1, 8, hour, minute, tzinfo=pytz.UTC)


def _spot(hour, minute, price, year=2026):
    return {
        "time": _at(hour, minute, year),
        "price_total": price,
        "price_sell": 0.02,
        "price_withtax": None,
    }


HOURLY = 2025  # a year with hourly prices


class TestQuarterPrices:
    """Each quarter takes the last price at or before its start."""

    def test_an_hourly_price_covers_its_four_quarters(self):
        points = [_spot(h, 0, p, HOURLY) for h, p in ((9, 0.10), (10, 0.20), (11, 0.30))]
        quarters = quarter_prices(points, _at(10, 0, HOURLY), _at(11, 0, HOURLY))
        assert [q["time"] for q in quarters] == [_at(10, m, HOURLY) for m in (0, 15, 30, 45)]
        assert [q["price_total"] for q in quarters] == [0.20] * 4

    def test_quarter_prices_are_taken_one_by_one(self):
        points = [_spot(10, m, 0.1 + m / 100) for m in (0, 15, 30, 45)] + [_spot(11, 0, 0.9)]
        quarters = quarter_prices(points, _at(10), _at(11))
        assert [q["price_total"] for q in quarters] == [0.10, 0.25, 0.40, 0.55]

    def test_a_missing_quarter_price_is_not_taken_from_the_quarter_before(self):
        points = [_spot(10, 0, 0.10), _spot(10, 30, 0.30), _spot(10, 45, 0.40)]
        quarters = quarter_prices(points, _at(10), _at(11))
        assert [q["price_total"] for q in quarters] == [0.10, None, 0.30, 0.40]

    def test_a_price_older_than_an_hour_is_not_in_force(self):
        quarters = quarter_prices(
            [_spot(9, 0, 0.10, HOURLY)], _at(10, 0, HOURLY), _at(11, 0, HOURLY)
        )
        assert quarters[0]["price_total"] is None


def _energy(hour, minute, consumption_wh, solar_wh=0.0, year=2026):
    return {
        "time": _at(hour, minute, year),
        "consumption_diff": consumption_wh,
        "solar_yield_diff": solar_wh,
        "battery_charge_diff": 0.0,
        "battery_discharge_diff": 0.0,
    }


QUARTER_SPOTS = [_spot(10, 15 * i, p) for i, p in enumerate((0.10, 0.20, 0.30, 0.40))]
# 300 Wh of solar in the first quarter, 300 Wh of use in the second
SOLAR_THEN_USE = [_energy(10, 5 * i, 0.0, 100.0) for i in range(3)] + [
    _energy(10, 5 * i, 100.0) for i in range(3, 6)
]


@pytest.fixture
def quarter_netting(monkeypatch):
    monkeypatch.setattr(quarter_prices_module, "QUARTER_NETTING_FROM", _at(0))


class TestBillPeriods:
    """While the meter nets by the hour, the bill prices the whole hour."""

    def test_an_hour_is_one_period_at_its_mean_quarter_price(self):
        periods = bill_periods(QUARTER_SPOTS, _at(10), _at(11))
        assert [(p["start"], p["stop"]) for p in periods] == [(_at(10), _at(11))]
        assert periods[0]["price_total"] == pytest.approx(0.25)

    def test_a_quarter_window_gets_the_price_of_its_hour(self):
        periods = bill_periods(QUARTER_SPOTS, _at(10, 15), _at(10, 30))
        assert [(p["start"], p["stop"]) for p in periods] == [(_at(10, 15), _at(10, 30))]
        assert periods[0]["price_total"] == pytest.approx(0.25)

    def test_an_hour_with_a_missing_quarter_price_has_no_price(self):
        periods = bill_periods(QUARTER_SPOTS[:3], _at(10), _at(11))
        assert periods[0]["price_total"] is None

    def test_quarter_netting_prices_each_quarter_by_itself(self, quarter_netting):
        periods = bill_periods(QUARTER_SPOTS, _at(10), _at(11))
        assert [p["start"] for p in periods] == [_at(10, m) for m in (0, 15, 30, 45)]
        assert [p["price_total"] for p in periods] == [0.10, 0.20, 0.30, 0.40]


class TestPricesAndCosts:
    """The costs of a window are the sum of its bill periods' costs."""

    def test_hourly_netting_offsets_solar_and_use_within_the_hour(self, aggregator):
        periods = bill_periods(QUARTER_SPOTS, _at(10), _at(11))
        result = aggregator._prices_and_costs(SOLAR_THEN_USE, periods)
        assert result["grid_import_cost"] == pytest.approx(0.0)
        assert result["solar_to_export"] == pytest.approx(0.0)
        assert result["price_total"] == pytest.approx(0.25)

    def test_quarter_netting_buys_and_sells_each_quarter(self, aggregator, quarter_netting):
        periods = bill_periods(QUARTER_SPOTS, _at(10), _at(11))
        result = aggregator._prices_and_costs(SOLAR_THEN_USE, periods)
        assert result["grid_import_cost"] == pytest.approx(0.3 * 0.20)
        assert result["solar_export_revenue"] == pytest.approx(0.3 * 0.02)

    def test_a_period_with_energy_but_no_price_leaves_out_all_costs(self, aggregator):
        periods = bill_periods(QUARTER_SPOTS[:3], _at(10), _at(11))
        emeters = [_energy(10, 5 * i, 100.0) for i in range(12)]
        assert aggregator._prices_and_costs(emeters, periods) == {}

    def test_a_quarter_without_energy_needs_no_price(self, aggregator, quarter_netting):
        periods = bill_periods(QUARTER_SPOTS[:3], _at(10), _at(11))
        emeters = [_energy(10, 5 * i, 100.0) for i in range(9)]
        result = aggregator._prices_and_costs(emeters, periods)
        assert result["grid_import_cost"] == pytest.approx(0.3 * (0.10 + 0.20 + 0.30))
