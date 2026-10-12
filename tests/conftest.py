"""Shared setup for every test run."""

import os

import dotenv

from src.common import config as app_config

# Modules read the config, and with it the install's .env, at import. Only
# an opted-in integration run may load it: on the Pi .env holds the real
# InfluxDB credentials and STAGING_MODE, which a CI run never has.
if os.environ.get("REDHOUSE_INTEGRATION") != "1":
    app_config.load_dotenv = dotenv.load_dotenv = lambda *args, **kwargs: False
