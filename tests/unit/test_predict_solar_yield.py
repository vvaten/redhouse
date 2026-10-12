"""The predictor reads history from emeters_5min and writes to emeters."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

SCRIPT = Path(__file__).resolve().parents[2] / "predict_solar_yield.py"


def _load():
    spec = importlib.util.spec_from_file_location("predict_solar_yield_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _client():
    record = MagicMock()
    record.get_time.return_value = pd.Timestamp("2026-10-12 10:00", tz="UTC")
    record.get_field.return_value = "Global radiation"
    record.get_value.return_value = 100.0
    table = MagicMock()
    table.records = [record]

    client = MagicMock()
    client.config.influxdb_bucket_weather = "weather_b"
    client.config.influxdb_bucket_emeters = "emeters_b"
    client.config.influxdb_bucket_emeters_5min = "emeters_5min_b"
    client.query_api.query.return_value = [table]
    return client


class TestHistoryQuery:
    def test_history_comes_from_emeters_5min_in_model_units(self):
        client = _client()
        _load().fetch_weather_and_emeters_data(client)
        queries = [c.args[0] for c in client.query_api.query.call_args_list]
        history = [q for q in queries if "solar_yield_avg" in q]
        assert len(history) == 1
        assert 'from(bucket: "emeters_5min_b")' in history[0]
        assert "_value / 3600.0" in history[0]

    def test_history_no_longer_needs_the_wibatemp_netting(self):
        client = _client()
        _load().fetch_weather_and_emeters_data(client)
        queries = " ".join(c.args[0] for c in client.query_api.query.call_args_list)
        assert '"emeters_b"' not in queries
        assert "emeter_avg" not in queries and "consumption_avg" not in queries


class TestPredictionWrite:
    def test_predictions_still_go_to_emeters(self):
        """The generator and wibatemp read the prediction from emeters."""
        client = _client()
        hour = pd.Timestamp("2026-10-12 10:00", tz="UTC")
        pred = pd.DataFrame({"solar_yield_avg_prediction": [0.5]}, index=[hour])
        _load().write_predictions_to_influxdb(pred, client)
        kwargs = client.write_with_retry.call_args.kwargs
        assert kwargs["bucket"] == "emeters_b"
        assert kwargs["record"][0]["time"] == hour + pd.Timedelta(minutes=30)
