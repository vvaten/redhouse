#!/usr/bin/env python
"""Fetch data needed for heating optimization from InfluxDB."""

import datetime

import pandas as pd

from src.common.config import get_config
from src.common.influx_client import InfluxClient
from src.common.logger import setup_logger

logger = setup_logger(__name__)


class HeatingDataFetcher:
    """
    Fetch weather forecasts, electricity prices, and solar predictions from InfluxDB.

    This data is used to optimize heating schedules based on:
    - Temperature forecasts (determines heating hours needed)
    - Electricity spot prices (determines when to heat)
    - Solar production forecasts (determines available free energy)
    """

    def __init__(self):
        """Initialize data fetcher with InfluxDB connection."""
        self.config = get_config()
        self.influx = InfluxClient(self.config)

    def fetch_heating_data(
        self, date_offset: int = 1, lookback_days: int = 1, lookahead_days: int = 2
    ) -> pd.DataFrame:
        """
        Fetch all data needed for heating optimization.

        Args:
            date_offset: Day offset from today (1 = tomorrow, 0 = today)
            lookback_days: Days to look back from date_offset
            lookahead_days: Days to look ahead from date_offset

        Returns:
            DataFrame with columns:
            - index: timestamp (UTC)
            - local_time: timestamp in Europe/Helsinki timezone
            - time_floor: timestamp rounded to hour
            - time_floor_local: time_floor in local timezone
            - Air temperature: forecast temperature (C)
            - price_total: total electricity price (c/kWh)
            - price_sell: price for selling back to grid (c/kWh)
            - solar_yield_avg_prediction: predicted solar production (kWh)
        """
        logger.info(
            f"Fetching heating data: offset={date_offset}, "
            f"lookback={lookback_days}, lookahead={lookahead_days}"
        )

        # Calculate time range
        start_offset = date_offset - lookback_days
        stop_offset = date_offset + lookahead_days

        # Fetch solar predictions
        solar_data = self._fetch_solar_predictions(start_offset, stop_offset)

        # Fetch spot prices
        price_data = self._fetch_spot_prices(start_offset, stop_offset)

        # Fetch weather forecast
        weather_data = self._fetch_weather_forecast(start_offset, stop_offset)

        # Merge all data into single DataFrame
        df = self._merge_data(solar_data, price_data, weather_data)

        logger.info(f"Fetched {len(df)} rows of heating data")

        return df

    def _fetch_solar_predictions(
        self, start_offset: int, stop_offset: int
    ) -> dict[datetime.datetime, dict]:
        """
        Fetch solar production predictions from InfluxDB.

        Args:
            start_offset: Start day offset
            stop_offset: Stop day offset

        Returns:
            Dict mapping timestamps to solar prediction data
        """
        query = f"""
        from(bucket: "{self.config.influxdb_bucket_emeters}")
          |> range(start: {start_offset}d, stop: {stop_offset}d)
          |> filter(fn: (r) => r["_field"] == "solar_yield_avg_prediction")
          |> aggregateWindow(every: 5m, fn: mean, createEmpty: false)
          |> yield(name: "mean")
        """

        try:
            result = self.influx.query_api.query(org=self.config.influxdb_org, query=query)

            data = {}
            for table in result:
                for record in table.records:
                    timestamp = record.get_time()
                    if timestamp not in data:
                        data[timestamp] = {}
                    data[timestamp]["solar_yield_avg_prediction"] = record.get_value()

            logger.debug(f"Fetched {len(data)} solar prediction records")
            return data

        except Exception as e:
            logger.error(f"Failed to fetch solar predictions: {e}")
            return {}

    def _fetch_spot_prices(
        self, start_offset: int, stop_offset: int
    ) -> dict[datetime.datetime, dict]:
        """
        Fetch electricity spot prices from InfluxDB.

        Args:
            start_offset: Start day offset
            stop_offset: Stop day offset

        Returns:
            Dict mapping timestamps to price data
        """
        query = f"""
        from(bucket: "{self.config.influxdb_bucket_spotprice}")
          |> range(start: {start_offset}d, stop: {stop_offset}d)
          |> filter(fn: (r) => r["_field"] == "price_total" or r["_field"] == "price_sell")
          |> aggregateWindow(every: 5m, fn: mean, createEmpty: false)
          |> yield(name: "mean")
        """

        try:
            result = self.influx.query_api.query(org=self.config.influxdb_org, query=query)

            data = {}
            for table in result:
                for record in table.records:
                    timestamp = record.get_time()
                    field = record.get_field()
                    if timestamp not in data:
                        data[timestamp] = {}
                    data[timestamp][field] = record.get_value()

            logger.debug(f"Fetched {len(data)} spot price records")
            return data

        except Exception as e:
            logger.error(f"Failed to fetch spot prices: {e}")
            return {}

    def _fetch_weather_forecast(
        self, start_offset: int, stop_offset: int
    ) -> dict[datetime.datetime, dict]:
        """
        Fetch weather forecast (temperature) from InfluxDB.

        Args:
            start_offset: Start day offset
            stop_offset: Stop day offset

        Returns:
            Dict mapping timestamps to weather data
        """
        query = f"""
        from(bucket: "{self.config.influxdb_bucket_weather}")
          |> range(start: {start_offset}d, stop: {stop_offset}d)
          |> filter(fn: (r) => r["_measurement"] == "weather")
          |> filter(fn: (r) => r["_field"] == "Air temperature")
          |> aggregateWindow(every: 15m, fn: mean, createEmpty: false)
          |> yield(name: "mean")
        """

        try:
            result = self.influx.query_api.query(org=self.config.influxdb_org, query=query)

            data = {}
            for table in result:
                for record in table.records:
                    timestamp = record.get_time()
                    if timestamp not in data:
                        data[timestamp] = {}
                    data[timestamp]["Air temperature"] = record.get_value()

            logger.debug(f"Fetched {len(data)} weather forecast records")
            return data

        except Exception as e:
            logger.error(f"Failed to fetch weather forecast: {e}")
            return {}

    def _merge_data(
        self,
        solar_data: dict,
        price_data: dict,
        weather_data: dict,
    ) -> pd.DataFrame:
        """
        Merge all data sources into a single DataFrame.

        Args:
            solar_data: Solar prediction data
            price_data: Spot price data
            weather_data: Weather forecast data

        Returns:
            Merged DataFrame with all data, sorted by timestamp
        """
        # Merge all dictionaries
        all_data = {}

        for timestamp, values in solar_data.items():
            if timestamp not in all_data:
                all_data[timestamp] = {}
            all_data[timestamp].update(values)

        for timestamp, values in price_data.items():
            if timestamp not in all_data:
                all_data[timestamp] = {}
            all_data[timestamp].update(values)

        for timestamp, values in weather_data.items():
            if timestamp not in all_data:
                all_data[timestamp] = {}
            all_data[timestamp].update(values)

        if not all_data:
            logger.warning("No data fetched from any source")
            return pd.DataFrame()

        # Convert to DataFrame
        df = pd.DataFrame(all_data).T.reset_index()

        # Add time columns
        df["local_time"] = df["index"].dt.tz_convert("Europe/Helsinki")
        df["time_floor"] = df["index"].dt.floor("H")
        df["time_floor_local"] = df["time_floor"].dt.tz_convert("Europe/Helsinki")

        # Sort by timestamp
        df = df.sort_values(by="index").reset_index(drop=True)

        return df

    def get_day_average_temperature(self, df: pd.DataFrame, date_offset: int = 1) -> float:
        """
        Get average temperature for a specific day.

        Args:
            df: DataFrame from fetch_heating_data()
            date_offset: Day offset (1 = tomorrow, 0 = today)

        Returns:
            Average temperature in Celsius
        """
        target_day = datetime.datetime.now() + datetime.timedelta(days=date_offset)
        next_day = target_day + datetime.timedelta(days=1)

        target_str = target_day.strftime("%Y-%m-%d")
        next_str = next_day.strftime("%Y-%m-%d")

        day_data = df[(df["time_floor_local"] >= target_str) & (df["time_floor_local"] < next_str)]

        if "Air temperature" in day_data.columns and len(day_data) > 0:
            avg_temp = day_data["Air temperature"].mean()
            logger.info(f"Average temperature for day offset {date_offset}: {avg_temp:.1f}C")
            return avg_temp
        else:
            logger.warning(f"No temperature data for day offset {date_offset}")
            return 0.0
