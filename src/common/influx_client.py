"""InfluxDB client wrapper for home automation system"""
import datetime
from typing import Any, Dict, List, Optional

import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS

from .config import get_config
from .logger import setup_logger


logger = setup_logger(__name__)


class InfluxClient:
    """Wrapper for InfluxDB client with common operations"""

    def __init__(self, config: Optional[Any] = None):
        """
        Initialize InfluxDB client

        Args:
            config: Configuration object (uses global config if None)
        """
        if config is None:
            config = get_config()

        self.config = config
        self.client = influxdb_client.InfluxDBClient(
            url=config.influxdb_url,
            token=config.influxdb_token,
            org=config.influxdb_org
        )
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        self.query_api = self.client.query_api()

    def write_temperatures(
        self,
        temperature_data: Dict[str, Dict[str, float]],
        timestamp: Optional[datetime.datetime] = None
    ) -> bool:
        """
        Write temperature data to InfluxDB

        Args:
            temperature_data: Dict of sensor_id -> {temp: value, updated: timestamp}
            timestamp: Timestamp for data point (default: now)

        Returns:
            True if successful
        """
        try:
            if timestamp is None:
                timestamp = datetime.datetime.utcnow()

            point = influxdb_client.Point("temperatures")

            for sensor_id, data in temperature_data.items():
                if 'temp' in data and data['temp'] is not None:
                    # Convert sensor ID to readable name
                    field_name = self._convert_sensor_id_to_name(sensor_id)
                    if field_name:
                        point = point.field(field_name, float(data['temp']))

            point = point.time(timestamp)

            self.write_api.write(
                bucket=self.config.influxdb_bucket_temperatures,
                org=self.config.influxdb_org,
                record=point
            )

            logger.debug(f"Written temperature data at {timestamp}")
            return True

        except Exception as e:
            logger.error(f"Exception when writing temperatures to InfluxDB: {e}")
            return False

    def write_humidities(
        self,
        humidity_data: Dict[str, Dict[str, float]],
        timestamp: Optional[datetime.datetime] = None
    ) -> bool:
        """
        Write humidity data to InfluxDB

        Args:
            humidity_data: Dict of sensor_id -> {hum: value}
            timestamp: Timestamp for data point (default: now)

        Returns:
            True if successful
        """
        try:
            if timestamp is None:
                timestamp = datetime.datetime.utcnow()

            point = influxdb_client.Point("humidities")

            for sensor_id, data in humidity_data.items():
                if 'hum' in data and data['hum'] is not None:
                    field_name = self._convert_sensor_id_to_name(sensor_id)
                    if field_name:
                        point = point.field(field_name, float(data['hum']))

            point = point.time(timestamp)

            self.write_api.write(
                bucket=self.config.influxdb_bucket_temperatures,
                org=self.config.influxdb_org,
                record=point
            )

            logger.debug(f"Written humidity data at {timestamp}")
            return True

        except Exception as e:
            logger.error(f"Exception when writing humidities to InfluxDB: {e}")
            return False

    def write_weather(self, weather_data: Dict[datetime.datetime, Dict[str, float]]) -> bool:
        """
        Write weather forecast data to InfluxDB

        Args:
            weather_data: Dict of timestamp -> {field: value}

        Returns:
            True if successful
        """
        try:
            points = []

            for timestamp, data in weather_data.items():
                point = influxdb_client.Point("weather")

                for field_name, value in data.items():
                    if value is not None:
                        point = point.field(field_name, float(value))

                point = point.time(timestamp)
                points.append(point)

            self.write_api.write(
                bucket=self.config.influxdb_bucket_weather,
                org=self.config.influxdb_org,
                record=points
            )

            logger.info(f"Written {len(points)} weather data points to DB")
            return True

        except Exception as e:
            logger.error(f"Exception when writing weather to InfluxDB: {e}")
            return False

    def write_spot_prices(self, spot_price_data: List[Dict[str, Any]]) -> bool:
        """
        Write spot price data to InfluxDB

        Args:
            spot_price_data: List of dicts with price data

        Returns:
            True if successful
        """
        try:
            points = []

            for entry in spot_price_data:
                timestamp = datetime.datetime.utcfromtimestamp(entry['epoch_timestamp'])

                point = (
                    influxdb_client.Point("spot")
                    .field("price", entry['price'])
                    .field("price_sell", entry['price_sell'])
                    .field("price_withtax", entry['price_withtax'])
                    .field("price_total", entry['price_total'])
                    .time(timestamp)
                )
                points.append(point)

            self.write_api.write(
                bucket=self.config.influxdb_bucket_spotprice,
                org=self.config.influxdb_org,
                record=points
            )

            logger.info(f"Written {len(points)} spot price points to DB")
            return True

        except Exception as e:
            logger.error(f"Exception when writing spot prices to InfluxDB: {e}")
            return False

    def query_heating_data(self, date_diff: int = 0) -> Any:
        """
        Query data needed for heating optimization

        Args:
            date_diff: Day offset from today (0=today, 1=tomorrow)

        Returns:
            Query results
        """
        # Implementation will be in heating optimizer module
        pass

    def _convert_sensor_id_to_name(self, sensor_id: str) -> Optional[str]:
        """
        Convert internal sensor ID to display name

        Args:
            sensor_id: Internal sensor ID (e.g., '28-xxxxx8a')

        Returns:
            Display name or None
        """
        sensor_mapping = self.config.get('sensor_mapping', {})

        # Try direct lookup
        if sensor_id in sensor_mapping:
            return sensor_mapping[sensor_id]

        # Try last 2 chars for DS18B20 sensors
        if sensor_id.startswith('28-'):
            suffix = sensor_id[-2:]
            for key, value in sensor_mapping.items():
                if key.endswith(suffix):
                    return value

        # Try last 3 chars for Shelly sensors
        if sensor_id.startswith('shelly-'):
            suffix = sensor_id[-3:]
            for key, value in sensor_mapping.items():
                if key.endswith(suffix):
                    return value

        logger.warning(f"No mapping found for sensor ID: {sensor_id}")
        return None

    def close(self):
        """Close InfluxDB client connection"""
        self.client.close()
