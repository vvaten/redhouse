"""Data freshness alert rule definitions.

Each rule names the redhouse timer that feeds its bucket, so production
rules can follow the migration instead of alerting on collectors that
are not running yet.
"""

ALERT_RULES = [
    {
        "name": "Temperature data stale",
        "timer": "redhouse-temperature",
        "bucket": "temperatures",
        "measurement": "temperatures",
        "max_age_minutes": 10,
        "eval_interval_seconds": 300,
        "skip_envs": ["staging"],
    },
    {
        "name": "Energy meter data stale",
        "timer": "redhouse-shelly-em3",
        "bucket": "shelly_em3_emeters_raw",
        "measurement": "shelly_em3",
        "wibatemp_bucket": "emeters",
        "wibatemp_measurement": "energy",
        "wibatemp_max_age_minutes": 30,
        "max_age_minutes": 5,
        "eval_interval_seconds": 120,
    },
    {
        "name": "CheckWatt data stale",
        "timer": "redhouse-checkwatt",
        "bucket": "checkwatt",
        "measurement": "checkwatt",
        "wibatemp_bucket": "checkwatt_full_data",
        "max_age_minutes": 120,
        "eval_interval_seconds": 600,
    },
    {
        "name": "Weather data stale",
        "timer": "redhouse-weather",
        "bucket": "weather",
        "measurement": "weather",
        "max_age_minutes": 720,
        "eval_interval_seconds": 1800,
    },
    {
        "name": "Wind power data stale",
        "timer": "redhouse-windpower",
        "bucket": "windpower",
        "measurement": "windpower",
        "max_age_minutes": 480,
        "eval_interval_seconds": 1800,
    },
    {
        "name": "5min aggregation stale",
        "timer": "redhouse-aggregate-emeters-5min",
        "bucket": "emeters_5min",
        "measurement": "energy",
        "max_age_minutes": 15,
        "eval_interval_seconds": 300,
        "skip_envs": ["wibatemp"],
    },
    {
        "name": "15min aggregation stale",
        "timer": "redhouse-aggregate-analytics-15min",
        "bucket": "analytics_15min",
        "measurement": "analytics",
        "max_age_minutes": 45,
        "eval_interval_seconds": 600,
        "skip_envs": ["wibatemp"],
    },
    {
        "name": "1hour aggregation stale",
        "timer": "redhouse-aggregate-analytics-1hour",
        "bucket": "analytics_1hour",
        "measurement": "analytics",
        "max_age_minutes": 180,
        "eval_interval_seconds": 1800,
        "skip_envs": ["wibatemp"],
    },
]

# Bucket name mapping from production to staging
STAGING_BUCKET_MAP = {
    "temperatures": "temperatures_staging",
    "shelly_em3_emeters_raw": "shelly_em3_emeters_raw_staging",
    "checkwatt": "checkwatt_staging",
    "weather": "weather_staging",
    "windpower": "windpower_staging",
    "emeters_5min": "emeters_5min_staging",
    "analytics_15min": "analytics_15min_staging",
    "analytics_1hour": "analytics_1hour_staging",
}
