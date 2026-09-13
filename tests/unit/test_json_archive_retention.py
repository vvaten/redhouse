"""Retention of the JSON archives that preserve forecast vintage.

The buckets overwrite forecasts per valid time, so these files are the
only record of what was predicted when. A collector left on the 7-day
default silently destroys training data.
"""

import re
from pathlib import Path

import pytest

SRC = Path(__file__).parents[2] / "src" / "data_collection"

# collector, expected retention_days. None means the 7-day default is fine
# because the data carries no forecast vintage worth keeping.
EXPECTED = {
    "weather.py": 30,
    "windpower.py": 30,
    "temperature.py": 30,
}


def override_in(path):
    """The retention_days value the collector sets, or None."""
    text = path.read_text(encoding="utf-8")
    match = re.search(r"json_logger\.retention_days\s*=\s*(\d+)", text)
    return int(match.group(1)) if match else None


@pytest.mark.parametrize("filename,expected", sorted(EXPECTED.items()))
def test_collector_sets_expected_retention(filename, expected):
    path = SRC / filename
    assert path.exists(), f"{filename} missing"
    assert override_in(path) == expected


def test_windpower_keeps_more_than_the_default():
    """Wind forecast vintage exists nowhere else and drives price spreads.

    The Pi window only has to outlast a failure of the nightly NAS
    mirror, which holds the real history.
    """
    assert override_in(SRC / "windpower.py") > 7


@pytest.mark.parametrize("filename", sorted(EXPECTED))
def test_override_precedes_log_data(filename):
    """Setting it after log_data would not affect the cleanup."""
    text = (SRC / filename).read_text(encoding="utf-8")
    assign = text.index("json_logger.retention_days")
    call = text.index("json_logger.log_data")
    assert assign < call


def test_forecast_collectors_beat_the_default():
    """Weather and wind vintage exists nowhere else once wibatemp stops.

    get_weather.py maintains a 30-day archive today. When Phase 1 moves
    weather to redhouse, a 7-day default would shrink that window.
    """
    for filename in ("weather.py", "windpower.py"):
        assert override_in(SRC / filename) >= 30, filename
