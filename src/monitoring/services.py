"""The redhouse timers the health check watches; an enabled one must be active."""

REDHOUSE_SERVICES = [
    "redhouse-temperature",
    "redhouse-weather",
    "redhouse-spot-prices",
    "redhouse-checkwatt",
    "redhouse-shelly-em3",
    "redhouse-windpower",
    "redhouse-pinglogger",
    "redhouse-aggregate-emeters-5min",
    "redhouse-aggregate-analytics-15min",
    "redhouse-aggregate-analytics-1hour",
    "redhouse-aggregate-network-1hour",
    "redhouse-solar-prediction",
    "redhouse-generate-program",
    "redhouse-execute-program",
    "redhouse-garage-heater",
    "redhouse-power-cap",
    "redhouse-load-guard",
    "redhouse-program-check",
    "redhouse-program-fallback",
]
