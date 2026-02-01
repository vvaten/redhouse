"""
Base class for data aggregation pipelines.

Provides common structure for all aggregation intervals (5min, 15min, 1hour).
"""

import datetime
from abc import ABC, abstractmethod
from typing import Optional

from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger

logger = setup_logger(__name__, "aggregation_base.log")


class AggregationPipeline(ABC):
    """
    Base class for data aggregation pipelines.

    Provides common structure for all aggregation intervals (5min, 15min, 1hour).
    """

    def __init__(self, influx_client: InfluxClient, config):
        """
        Initialize aggregation pipeline.

        Args:
            influx_client: InfluxDB client for data operations
            config: Configuration object
        """
        self.influx = influx_client
        self.config = config

    def aggregate_window(
        self,
        window_start: datetime.datetime,
        window_end: datetime.datetime,
        write_to_influx: bool = True,
    ) -> Optional[dict]:
        """
        Execute aggregation pipeline for a time window.

        Args:
            window_start: Window start time
            window_end: Window end time
            write_to_influx: Whether to write results to InfluxDB

        Returns:
            Aggregated metrics dict or None if failed
        """
        try:
            # Step 1: Fetch data
            raw_data = self.fetch_data(window_start, window_end)
            if not raw_data:
                logger.warning(f"No data for window {window_start} - {window_end}")
                return None

            # Step 2: Validate data
            if not self.validate_data(raw_data):
                logger.error(f"Data validation failed for window {window_start} - {window_end}")
                return None

            # Step 3: Calculate metrics (implemented by subclass)
            metrics = self.calculate_metrics(raw_data, window_start, window_end)
            if not metrics:
                logger.error(f"Metric calculation failed for window {window_start} - {window_end}")
                return None

            # Step 4: Write results
            if write_to_influx:
                success = self.write_results(metrics, window_end)
                if not success:
                    logger.error(
                        f"Failed to write results for window {window_start} - {window_end}"
                    )
                    return None

            return metrics

        except Exception as e:
            logger.error(f"Aggregation failed for window {window_start} - {window_end}: {e}")
            return None

    @abstractmethod
    def fetch_data(self, window_start: datetime.datetime, window_end: datetime.datetime) -> dict:
        """
        Fetch raw data for the window.

        Args:
            window_start: Start of time window
            window_end: End of time window

        Returns:
            Dictionary with raw data from various sources
        """
        pass

    @abstractmethod
    def validate_data(self, raw_data: dict) -> bool:
        """
        Validate fetched data.

        Args:
            raw_data: Dictionary with raw data from fetch_data

        Returns:
            True if data is valid for aggregation
        """
        pass

    @abstractmethod
    def calculate_metrics(
        self,
        raw_data: dict,
        window_start: datetime.datetime,
        window_end: datetime.datetime,
    ) -> Optional[dict]:
        """
        Calculate aggregated metrics.

        Args:
            raw_data: Dictionary with validated raw data
            window_start: Start of time window
            window_end: End of time window

        Returns:
            Dictionary of calculated metrics or None if calculation failed
        """
        pass

    @abstractmethod
    def write_results(self, metrics: dict, timestamp: datetime.datetime) -> bool:
        """
        Write results to InfluxDB.

        Args:
            metrics: Dictionary of calculated metrics
            timestamp: Timestamp for the data point

        Returns:
            True if write successful
        """
        pass
