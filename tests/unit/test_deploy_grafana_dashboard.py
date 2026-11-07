"""The dashboard deploy sends the sensor names as they are in the JSON file."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deployment" / "deploy_grafana_dashboard.py"


def _load():
    spec = importlib.util.spec_from_file_location("deploy_grafana_dashboard_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_umlaut_sensor_names_reach_grafana_unchanged(monkeypatch):
    monkeypatch.setenv("GRAFANA_URL", "http://grafana.invalid")
    monkeypatch.setenv("GRAFANA_API_KEY", "dummy")
    module = _load()
    monkeypatch.setattr(module, "load_dotenv", lambda: None)
    response = MagicMock(ok=True)
    response.json.return_value = {"url": "/d/x"}
    with (
        patch.object(sys, "argv", ["deploy_grafana_dashboard.py", "production"]),
        patch.object(module.requests, "post", return_value=response) as post,
    ):
        module.main()
    sent = json.dumps(post.call_args.kwargs["json"]["dashboard"], ensure_ascii=False)
    expected = (REPO / "grafana" / "dashboards" / "production_dashboard.json").read_text(
        encoding="utf-8"
    )
    # The file holds a-umlauts; cp1252 would turn each into two characters
    assert "\u00e4" in json.dumps(json.loads(expected), ensure_ascii=False)
    assert "\u00c3" not in sent
    assert json.loads(sent)["panels"] == json.loads(expected)["panels"]
