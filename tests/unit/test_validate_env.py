"""validate_env.py refuses a .env that lacks a bucket or, in staging, names a production one."""

import importlib.util
import os
import re
from pathlib import Path
from unittest.mock import patch

import dotenv.main

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
    if not staging:
        values.update((key, key.lower()) for key in module.PRODUCTION_ONLY_KEYS)
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


def test_production_needs_the_network_bucket_and_staging_does_not(tmp_path):
    valid, errors, _ = _validate(tmp_path, staging=False, drop=["INFLUXDB_BUCKET_NETWORK"])
    assert not valid
    assert any("INFLUXDB_BUCKET_NETWORK" in e for e in errors)
    assert _validate(tmp_path, staging=True)[0]


def test_every_bucket_key_the_config_requires_is_checked():
    config = (SCRIPT.parents[1] / "src" / "common" / "config.py").read_text(encoding="utf-8")
    needed = set(re.findall(r'_require_env\("(INFLUXDB_BUCKET_\w+)"\)', config))
    module = _load()
    assert needed == set(module.REQUIRED_KEYS["influxdb_buckets"]) | set(
        module.PRODUCTION_ONLY_KEYS
    )


def test_staging_may_not_name_a_production_bucket(tmp_path):
    override = {"INFLUXDB_BUCKET_SOLAR_PREDICTION": "solar_prediction"}
    valid, errors, _ = _validate(tmp_path, staging=True, override=override)
    assert not valid
    assert any("INFLUXDB_BUCKET_SOLAR_PREDICTION=solar_prediction" in e for e in errors)


def test_staging_may_not_name_the_production_network_bucket(tmp_path):
    override = {"INFLUXDB_BUCKET_NETWORK": "network"}
    valid, errors, _ = _validate(tmp_path, staging=True, override=override)
    assert not valid
    assert any("INFLUXDB_BUCKET_NETWORK=network" in e for e in errors)


def test_staging_reads_the_production_temperatures(tmp_path):
    override = {"INFLUXDB_BUCKET_TEMPERATURES": "temperatures"}
    assert _validate(tmp_path, staging=True, override=override)[0]
