"""
Unit tests for AggregationPipeline base class.

Tests the base aggregation pipeline functionality using a concrete test implementation.
"""

import datetime
from unittest.mock import MagicMock, Mock

import pytest
import pytz

from src.aggregation.aggregation_base import AggregationPipeline
from src.common.config import get_config
from src.common.influx_client import InfluxClient


class TestAggregator(AggregationPipeline):
    """
    Concrete test implementation of AggregationPipeline.

    Used to test the base class functionality.
    """

    def __init__(self, influx_client, config):
        super().__init__(influx_client, config)
        self.fetch_called = False
        self.validate_called = False
        self.calculate_called = False
        self.write_called = False

    def fetch_data(self, window_start, window_end):
        self.fetch_called = True
        if not hasattr(self, "fetch_return_value"):
            return {"test_data": [1, 2, 3]}
        return self.fetch_return_value

    def validate_data(self, raw_data):
        self.validate_called = True
        if not hasattr(self, "validate_return_value"):
            return True
        return self.validate_return_value

    def calculate_metrics(self, raw_data, window_start, window_end):
        self.calculate_called = True
        if not hasattr(self, "calculate_return_value"):
            return {"metric1": 100.0, "metric2": 200.0}
        return self.calculate_return_value

    def write_results(self, metrics, timestamp):
        self.write_called = True
        if not hasattr(self, "write_return_value"):
            return True
        return self.write_return_value


@pytest.fixture
def mock_influx_client():
    """Create a mock InfluxDB client."""
    client = Mock(spec=InfluxClient)
    client.query_api = MagicMock()
    client.write_api = MagicMock()
    return client


@pytest.fixture
def config():
    """Get the configuration."""
    return get_config()


@pytest.fixture
def test_aggregator(mock_influx_client, config):
    """Create a test aggregator instance."""
    return TestAggregator(mock_influx_client, config)


@pytest.fixture
def time_window():
    """Create a test time window."""
    tz = pytz.timezone("Europe/Helsinki")
    window_start = tz.localize(datetime.datetime(2024, 1, 15, 10, 0, 0))
    window_end = tz.localize(datetime.datetime(2024, 1, 15, 10, 5, 0))
    return window_start, window_end


class TestAggregationPipeline:
    """Test the AggregationPipeline base class."""

    def test_initialization(self, test_aggregator, mock_influx_client, config):
        """Test that aggregator is initialized correctly."""
        assert test_aggregator.influx == mock_influx_client
        assert test_aggregator.config == config

    def test_successful_aggregation(self, test_aggregator, time_window):
        """Test successful aggregation through the entire pipeline."""
        window_start, window_end = time_window

        result = test_aggregator.aggregate_window(window_start, window_end, write_to_influx=True)

        assert result is not None
        assert result == {"metric1": 100.0, "metric2": 200.0}
        assert test_aggregator.fetch_called
        assert test_aggregator.validate_called
        assert test_aggregator.calculate_called
        assert test_aggregator.write_called

    def test_aggregation_without_write(self, test_aggregator, time_window):
        """Test aggregation without writing to InfluxDB."""
        window_start, window_end = time_window

        result = test_aggregator.aggregate_window(window_start, window_end, write_to_influx=False)

        assert result is not None
        assert result == {"metric1": 100.0, "metric2": 200.0}
        assert test_aggregator.fetch_called
        assert test_aggregator.validate_called
        assert test_aggregator.calculate_called
        assert not test_aggregator.write_called

    def test_aggregation_with_no_data(self, test_aggregator, time_window):
        """Test aggregation when fetch returns no data."""
        window_start, window_end = time_window
        test_aggregator.fetch_return_value = None

        result = test_aggregator.aggregate_window(window_start, window_end)

        assert result is None
        assert test_aggregator.fetch_called
        assert not test_aggregator.validate_called
        assert not test_aggregator.calculate_called
        assert not test_aggregator.write_called

    def test_aggregation_with_empty_data(self, test_aggregator, time_window):
        """Test aggregation when fetch returns empty data."""
        window_start, window_end = time_window
        test_aggregator.fetch_return_value = {}

        result = test_aggregator.aggregate_window(window_start, window_end)

        assert result is None
        assert test_aggregator.fetch_called
        assert not test_aggregator.validate_called

    def test_aggregation_with_validation_failure(self, test_aggregator, time_window):
        """Test aggregation when validation fails."""
        window_start, window_end = time_window
        test_aggregator.validate_return_value = False

        result = test_aggregator.aggregate_window(window_start, window_end)

        assert result is None
        assert test_aggregator.fetch_called
        assert test_aggregator.validate_called
        assert not test_aggregator.calculate_called
        assert not test_aggregator.write_called

    def test_aggregation_with_calculation_failure(self, test_aggregator, time_window):
        """Test aggregation when calculation returns None."""
        window_start, window_end = time_window
        test_aggregator.calculate_return_value = None

        result = test_aggregator.aggregate_window(window_start, window_end)

        assert result is None
        assert test_aggregator.fetch_called
        assert test_aggregator.validate_called
        assert test_aggregator.calculate_called
        assert not test_aggregator.write_called

    def test_aggregation_with_calculation_empty_dict(self, test_aggregator, time_window):
        """Test aggregation when calculation returns empty dict."""
        window_start, window_end = time_window
        test_aggregator.calculate_return_value = {}

        result = test_aggregator.aggregate_window(window_start, window_end)

        assert result is None
        assert test_aggregator.fetch_called
        assert test_aggregator.validate_called
        assert test_aggregator.calculate_called
        assert not test_aggregator.write_called

    def test_aggregation_with_write_failure(self, test_aggregator, time_window):
        """Test aggregation when write fails."""
        window_start, window_end = time_window
        test_aggregator.write_return_value = False

        result = test_aggregator.aggregate_window(window_start, window_end, write_to_influx=True)

        assert result is None
        assert test_aggregator.fetch_called
        assert test_aggregator.validate_called
        assert test_aggregator.calculate_called
        assert test_aggregator.write_called

    def test_aggregation_with_exception_in_fetch(self, test_aggregator, time_window):
        """Test aggregation when fetch raises an exception."""
        window_start, window_end = time_window

        def fetch_with_exception(ws, we):
            raise RuntimeError("Fetch failed")

        test_aggregator.fetch_data = fetch_with_exception

        result = test_aggregator.aggregate_window(window_start, window_end)

        assert result is None

    def test_aggregation_with_exception_in_calculate(self, test_aggregator, time_window):
        """Test aggregation when calculate raises an exception."""
        window_start, window_end = time_window

        def calculate_with_exception(rd, ws, we):
            raise RuntimeError("Calculation failed")

        test_aggregator.calculate_metrics = calculate_with_exception

        result = test_aggregator.aggregate_window(window_start, window_end)

        assert result is None

    def test_abstract_methods_must_be_implemented(self):
        """Test that abstract methods must be implemented by subclasses."""
        # This test verifies that we cannot instantiate AggregationPipeline directly
        mock_influx = Mock(spec=InfluxClient)
        config = get_config()

        with pytest.raises(TypeError):
            # Should raise TypeError because abstract methods are not implemented
            AggregationPipeline(mock_influx, config)
