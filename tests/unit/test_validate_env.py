"""validate_env.py refuses a .env that lacks a bucket or, in staging, names a production one."""

import importlib.util
import os
import re
from pathlib import Path
from unittest.mock import patch

import dotenv.main
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate_env.py"


def _load():
    spec = importlib.util.spec_from_file_location("validate_env_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # conftest stubs dotenv.load_dotenv; these tests load only their own file
    module.load_dotenv = dotenv.main.load_dotenv
    return module


def _validate(tmp_path, staging, drop=(), override=None):
    module = _load()
    values = {}
    for keys in module.REQUIRED_KEYS.values():
        for key in keys:
            values[key] = key.lower()
    for key in module.REQUIRED_KEYS["influxdb_buckets"]:
        if staging and key != "INFLUXDB_BUCKET_TEMPERATURES":
            values[key] += "_staging"
    values["STAGING_MODE"] = "true" if staging else "false"
    values.update(override or {})
    for key in drop:
        del values[key]
    env = tmp_path / ".env"
    env.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    with patch.dict(os.environ, {}, clear=True):
        return module.validate_env_file(str(env))


def test_a_complete_env_is_valid(tmp_path):
    assert _validate(tmp_path, staging=False)[0]
    assert _validate(tmp_path, staging=True)[0]


def test_a_missing_bucket_key_is_an_error(tmp_path):
    valid, errors, _ = _validate(tmp_path, staging=False, drop=["INFLUXDB_BUCKET_SOLAR_PREDICTION"])
    assert not valid
    assert any("INFLUXDB_BUCKET_SOLAR_PREDICTION" in e for e in errors)


@pytest.mark.parametrize("staging", [False, True])
@pytest.mark.parametrize("key", ["INFLUXDB_BUCKET_NETWORK", "INFLUXDB_BUCKET_NETWORK_1HOUR"])
def test_both_environments_need_the_network_buckets(tmp_path, staging, key):
    """The hourly summary runs in staging too, reading the production ping data."""
    valid, errors, _ = _validate(tmp_path, staging=staging, drop=[key])
    assert not valid
    assert any(key in e for e in errors)


def test_every_bucket_key_the_config_requires_is_checked():
    config = (SCRIPT.parents[1] / "src" / "common" / "config.py").read_text(encoding="utf-8")
    needed = set(re.findall(r'_require_env\("(INFLUXDB_BUCKET_\w+)"\)', config))
    assert needed == set(_load().REQUIRED_KEYS["influxdb_buckets"])


def test_staging_may_not_name_a_production_bucket(tmp_path):
    override = {"INFLUXDB_BUCKET_SOLAR_PREDICTION": "solar_prediction"}
    valid, errors, _ = _validate(tmp_path, staging=True, override=override)
    assert not valid
    assert any("INFLUXDB_BUCKET_SOLAR_PREDICTION=solar_prediction" in e for e in errors)


def test_staging_may_not_name_the_production_hourly_network_bucket(tmp_path):
    override = {"INFLUXDB_BUCKET_NETWORK_1HOUR": "network_1hour"}
    valid, errors, _ = _validate(tmp_path, staging=True, override=override)
    assert not valid
    assert any("INFLUXDB_BUCKET_NETWORK_1HOUR=network_1hour" in e for e in errors)


def test_staging_reads_the_production_temperatures_and_ping_data(tmp_path):
    override = {
        "INFLUXDB_BUCKET_TEMPERATURES": "temperatures",
        "INFLUXDB_BUCKET_NETWORK": "network",
    }
    assert _validate(tmp_path, staging=True, override=override)[0]
