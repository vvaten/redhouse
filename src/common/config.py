"""Configuration management for home automation system"""

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv


class Config:
    """Configuration manager that loads from .env and config.yaml"""

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

        # Load YAML config
        if config_path is None:
            project_root = Path(__file__).parent.parent.parent
            config_path = str(project_root / "config" / "config.yaml")

        self._yaml_config: dict[str, Any] = {}
        if os.path.exists(config_path):
            with open(config_path) as f:
                self._yaml_config = yaml.safe_load(f) or {}

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value by key. Supports dot notation for nested values.

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
        keys = key.split(".")
        value = self._yaml_config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    # InfluxDB configuration
    @property
    def influxdb_url(self) -> str:
        url = self.get("INFLUXDB_URL")
        if not url:
            raise ValueError("INFLUXDB_URL is not configured in .env!")
        return url

    @property
    def influxdb_token(self) -> str:
        token = self.get("INFLUXDB_TOKEN")
        if not token:
            raise ValueError("INFLUXDB_TOKEN is not configured in .env!")
        return token

    @property
    def influxdb_org(self) -> str:
        org = self.get("INFLUXDB_ORG")
        if not org:
            raise ValueError("INFLUXDB_ORG is not configured in .env!")
        return org

    @property
    def influxdb_bucket_temperatures(self) -> str:
        bucket = self.get("INFLUXDB_BUCKET_TEMPERATURES")
        if not bucket:
            raise ValueError("INFLUXDB_BUCKET_TEMPERATURES is not configured in .env!")
        return bucket

    @property
    def influxdb_bucket_weather(self) -> str:
        bucket = self.get("INFLUXDB_BUCKET_WEATHER")
        if not bucket:
            raise ValueError("INFLUXDB_BUCKET_WEATHER is not configured in .env!")
        return bucket

    @property
    def influxdb_bucket_spotprice(self) -> str:
        bucket = self.get("INFLUXDB_BUCKET_SPOTPRICE")
        if not bucket:
            raise ValueError("INFLUXDB_BUCKET_SPOTPRICE is not configured in .env!")
        return bucket

    @property
    def influxdb_bucket_emeters(self) -> str:
        bucket = self.get("INFLUXDB_BUCKET_EMETERS")
        if not bucket:
            raise ValueError("INFLUXDB_BUCKET_EMETERS is not configured in .env!")
        return bucket

    @property
    def influxdb_bucket_checkwatt(self) -> str:
        bucket = self.get("INFLUXDB_BUCKET_CHECKWATT")
        if not bucket:
            raise ValueError("INFLUXDB_BUCKET_CHECKWATT is not configured in .env!")
        return bucket

    @property
    def influxdb_bucket_shelly_em3_raw(self) -> str:
        bucket = self.get("INFLUXDB_BUCKET_SHELLY_EM3_RAW")
        if not bucket:
            raise ValueError("INFLUXDB_BUCKET_SHELLY_EM3_RAW is not configured in .env!")
        return bucket

    @property
    def influxdb_bucket_emeters_5min(self) -> str:
        bucket = self.get("INFLUXDB_BUCKET_EMETERS_5MIN")
        if not bucket:
            raise ValueError("INFLUXDB_BUCKET_EMETERS_5MIN is not configured in .env!")
        return bucket

    @property
    def influxdb_bucket_analytics_15min(self) -> str:
        bucket = self.get("INFLUXDB_BUCKET_ANALYTICS_15MIN")
        if not bucket:
            raise ValueError("INFLUXDB_BUCKET_ANALYTICS_15MIN is not configured in .env!")
        return bucket

    @property
    def influxdb_bucket_analytics_1hour(self) -> str:
        bucket = self.get("INFLUXDB_BUCKET_ANALYTICS_1HOUR")
        if not bucket:
            raise ValueError("INFLUXDB_BUCKET_ANALYTICS_1HOUR is not configured in .env!")
        return bucket

    @property
    def influxdb_bucket_windpower(self) -> str:
        bucket = self.get("INFLUXDB_BUCKET_WINDPOWER")
        if not bucket:
            raise ValueError("INFLUXDB_BUCKET_WINDPOWER is not configured in .env!")
        return bucket

    @property
    def influxdb_bucket_load_control(self) -> str:
        bucket = self.get("INFLUXDB_BUCKET_LOAD_CONTROL")
        if not bucket:
            raise ValueError(
                "INFLUXDB_BUCKET_LOAD_CONTROL is not configured! "
                "Set it in .env to 'load_control_staging' (staging) or 'load_control' (production)"
            )
        return bucket

    # Weather configuration
    @property
    def weather_latlon(self) -> str:
        return self.get("WEATHER_LATLON", "60.1699,24.9384")

    # API Keys
    @property
    def fingrid_api_key(self) -> str:
        return self.get("FINGRID_API_KEY", "")

    # Hardware configuration
    @property
    def pump_i2c_bus(self) -> int:
        return int(self.get("PUMP_I2C_BUS", 1))

    @property
    def pump_i2c_address(self) -> int:
        addr = self.get("PUMP_I2C_ADDRESS", "0x10")
        return int(addr, 16) if isinstance(addr, str) else addr

    @property
    def shelly_relay_url(self) -> str:
        return self.get("SHELLY_RELAY_URL", "http://192.168.1.5")

    # Heating configuration
    @property
    def heating_curve(self) -> dict[int, float]:
        curve = self.get("heating.curve", {-20: 12, 0: 8, 16: 4})
        return {int(k): float(v) for k, v in curve.items()}

    @property
    def evuoff_threshold_price(self) -> float:
        return float(self.get("heating.evuoff_threshold_price", 0.20))

    @property
    def evuoff_max_continuous_hours(self) -> int:
        return int(self.get("heating.evuoff_max_continuous_hours", 4))

    # Logging configuration
    @property
    def log_level(self) -> str:
        return self.get("LOG_LEVEL", "INFO")

    @property
    def log_dir(self) -> str:
        return self.get("LOG_DIR", "/var/log/redhouse")

    @property
    def log_max_bytes(self) -> int:
        return int(self.get("LOG_MAX_BYTES", 10485760))

    @property
    def log_backup_count(self) -> int:
        return int(self.get("LOG_BACKUP_COUNT", 5))


# Global config instance
_config = None


def get_config() -> Config:
    """Get global configuration instance"""
    global _config
    if _config is None:
        _config = Config()
    return _config
