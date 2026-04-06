"""Configuration management for home automation system"""

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv


class Config:
    """Configuration manager that loads from .env, config.yaml, and sensors.yaml"""

    def __init__(self, config_path: Optional[str] = None, env_path: Optional[str] = None):
        """
        Initialize configuration

        Args:
            config_path: Path to config.yaml (default: config/config.yaml)
            env_path: Path to .env file (default: .env in project root)
        """
        # Load environment variables
        if env_path:
            load_dotenv(env_path)
        else:
            load_dotenv()

        # Load YAML config (required)
        if config_path is None:
            project_root = Path(__file__).parent.parent.parent
            config_path = str(project_root / "config" / "config.yaml")

        if not os.path.exists(config_path):
            raise FileNotFoundError(
                f"Required configuration file not found: {config_path}\n"
                f"Copy config/config.yaml.example to config/config.yaml and adjust values."
            )

        with open(config_path) as f:
            self._yaml_config: dict[str, Any] = yaml.safe_load(f) or {}

        # Load sensor mapping (optional, separate file for PII)
        self._sensor_mapping: dict[str, str] = {}
        sensors_path = str(Path(config_path).parent / "sensors.yaml")
        if os.path.exists(sensors_path):
            with open(sensors_path) as f:
                sensors_data = yaml.safe_load(f) or {}
                self._sensor_mapping = sensors_data.get("sensor_mapping", {})

    def _get_yaml(self, key: str) -> Any:
        """
        Get value from YAML config using dot notation.

        Args:
            key: Dot-separated key (e.g., 'heating.curve')

        Returns:
            Value from YAML config, or None if not found
        """
        keys = key.split(".")
        value = self._yaml_config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return None
        return value

    def _require_yaml(self, key: str) -> Any:
        """
        Get value from YAML config, raising error if missing.

        Args:
            key: Dot-separated key (e.g., 'heating.curve')

        Returns:
            Value from YAML config

        Raises:
            ValueError: If key is not found in config.yaml
        """
        value = self._get_yaml(key)
        if value is None:
            raise ValueError(f"Required config key '{key}' not found in config.yaml")
        return value

    def _require_env(self, key: str) -> str:
        """
        Get value from environment variable, raising error if missing.

        Args:
            key: Environment variable name

        Returns:
            Value from environment

        Raises:
            ValueError: If environment variable is not set
        """
        value = os.getenv(key)
        if not value:
            raise ValueError(f"Required environment variable '{key}' is not set in .env")
        return value

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value by key. Checks env vars first, then YAML config.

        Args:
            key: Configuration key (e.g., 'heating.curve' or 'INFLUXDB_URL')
            default: Default value if key not found

        Returns:
            Configuration value
        """
        # Try environment variable first (uppercase)
        env_key = key.upper().replace(".", "_")
        env_value = os.getenv(env_key)
        if env_value is not None:
            return env_value

        # Try YAML config
        yaml_value = self._get_yaml(key)
        if yaml_value is not None:
            return yaml_value

        return default

    # InfluxDB configuration (from .env)
    @property
    def influxdb_url(self) -> str:
        return self._require_env("INFLUXDB_URL")

    @property
    def influxdb_token(self) -> str:
        return self._require_env("INFLUXDB_TOKEN")

    @property
    def influxdb_org(self) -> str:
        return self._require_env("INFLUXDB_ORG")

    @property
    def influxdb_bucket_temperatures(self) -> str:
        return self._require_env("INFLUXDB_BUCKET_TEMPERATURES")

    @property
    def influxdb_bucket_weather(self) -> str:
        return self._require_env("INFLUXDB_BUCKET_WEATHER")

    @property
    def influxdb_bucket_spotprice(self) -> str:
        return self._require_env("INFLUXDB_BUCKET_SPOTPRICE")

    @property
    def influxdb_bucket_emeters(self) -> str:
        return self._require_env("INFLUXDB_BUCKET_EMETERS")

    @property
    def influxdb_bucket_checkwatt(self) -> str:
        return self._require_env("INFLUXDB_BUCKET_CHECKWATT")

    @property
    def influxdb_bucket_shelly_em3_raw(self) -> str:
        return self._require_env("INFLUXDB_BUCKET_SHELLY_EM3_RAW")

    @property
    def influxdb_bucket_emeters_5min(self) -> str:
        return self._require_env("INFLUXDB_BUCKET_EMETERS_5MIN")

    @property
    def influxdb_bucket_analytics_15min(self) -> str:
        return self._require_env("INFLUXDB_BUCKET_ANALYTICS_15MIN")

    @property
    def influxdb_bucket_analytics_1hour(self) -> str:
        return self._require_env("INFLUXDB_BUCKET_ANALYTICS_1HOUR")

    @property
    def influxdb_bucket_windpower(self) -> str:
        return self._require_env("INFLUXDB_BUCKET_WINDPOWER")

    @property
    def influxdb_bucket_load_control(self) -> str:
        return self._require_env("INFLUXDB_BUCKET_LOAD_CONTROL")

    # Weather configuration (from .env - PII)
    @property
    def weather_latlon(self) -> str:
        return self._require_env("WEATHER_LATLON")

    # API Keys (from .env)
    @property
    def fingrid_api_key(self) -> str:
        return self._require_env("FINGRID_API_KEY")

    # Hardware configuration (from config.yaml)
    @property
    def pump_i2c_bus(self) -> int:
        return int(self._require_yaml("hardware.pump_i2c_bus"))

    @property
    def pump_i2c_address(self) -> int:
        addr = self._require_yaml("hardware.pump_i2c_address")
        return int(addr, 16) if isinstance(addr, str) else addr

    @property
    def shelly_relay_url(self) -> str:
        return str(self._require_yaml("hardware.shelly_relay_url"))

    @property
    def shelly_em3_url(self) -> str:
        return str(self._require_yaml("hardware.shelly_em3_url"))

    # Heating configuration (from config.yaml)
    @property
    def heating_curve(self) -> dict[int, float]:
        curve = self._require_yaml("heating.curve")
        return {int(k): float(v) for k, v in curve.items()}

    @property
    def evuoff_threshold_price(self) -> float:
        return float(self._require_yaml("heating.evuoff_threshold_price"))

    @property
    def evuoff_max_continuous_hours(self) -> int:
        return int(self._require_yaml("heating.evuoff_max_continuous_hours"))

    # Spot price configuration (from config.yaml)
    @property
    def spot_prices_config(self) -> dict[str, float]:
        cfg = self._require_yaml("data_collection.spot_prices")
        return {
            "value_added_tax": float(cfg["value_added_tax"]),
            "sellers_margin": float(cfg["sellers_margin"]),
            "production_buyback_margin": float(cfg["production_buyback_margin"]),
            "transfer_day_price": float(cfg["transfer_day_price"]),
            "transfer_night_price": float(cfg["transfer_night_price"]),
            "transfer_tax_price": float(cfg["transfer_tax_price"]),
        }

    # Sensor mapping (from sensors.yaml)
    @property
    def sensor_mapping(self) -> dict[str, str]:
        return self._sensor_mapping

    # Logging configuration (from config.yaml with sensible defaults)
    @property
    def log_level(self) -> str:
        return str(self._get_yaml("logging.level") or "INFO")

    @property
    def log_dir(self) -> str:
        return str(self._get_yaml("logging.dir") or "/var/log/redhouse")

    @property
    def log_max_bytes(self) -> int:
        return int(self._get_yaml("logging.max_bytes") or 10485760)

    @property
    def log_backup_count(self) -> int:
        return int(self._get_yaml("logging.backup_count") or 5)


# Global config instance
_config = None


def get_config() -> Config:
    """Get global configuration instance"""
    global _config
    if _config is None:
        _config = Config()
    return _config
