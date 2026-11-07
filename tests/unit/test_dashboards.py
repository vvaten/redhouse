"""The Grafana dashboards keep their "now" cursor and solar prediction line."""

import json
from pathlib import Path

import pytest

DASHBOARDS = Path(__file__).parents[2] / "grafana" / "dashboards"
CURSOR_PANELS = {
    "Spot Price",
    "Local Weather Forecast",
    "Energy Meters",
    "CheckWatt",
    "Water Temperature",
    "Wind Power (Finland) MW",
    "Heating Schedule (Geothermal Pump)",
}
PREDICTION_BUCKET = {
    "production_dashboard.json": "solar_prediction",
    "staging_dashboard.json": "solar_prediction_staging",
}


def _panels(name):
    def walk(node):
        for panel in node.get("panels", []):
            yield panel
            yield from walk(panel)

    dashboard = json.loads((DASHBOARDS / name).read_text(encoding="utf-8"))
    return {panel["title"]: panel for panel in walk(dashboard) if "title" in panel}


def _target(panel, ref):
    return next(t for t in panel["targets"] if t["refId"] == ref)


def _override(panel, matcher_id, options):
    overrides = panel["fieldConfig"]["overrides"]
    return next(o for o in overrides if o["matcher"] == {"id": matcher_id, "options": options})


@pytest.mark.parametrize("name", sorted(PREDICTION_BUCKET))
def test_every_forecast_panel_has_a_hidden_now_cursor(name):
    panels = _panels(name)
    for title in CURSOR_PANELS:
        query = _target(panels[title], "Now")["query"]
        assert "now()" in query and "array.from" in query, title
        props = {
            p["id"]: p["value"]
            for p in _override(panels[title], "byFrameRefID", "Now")["properties"]
        }
        assert props["custom.hideFrom"] == {"legend": True, "tooltip": True, "viz": False}, title
        # Its own hidden 0..1 axis, so it spans the panel and moves no data axis
        assert (props["custom.axisPlacement"], props["min"], props["max"]) == ("hidden", 0, 1)
        assert "current_time: 0}" in query and "current_time: 1}" in query, title


@pytest.mark.parametrize("name", sorted(PREDICTION_BUCKET))
def test_energy_meters_shows_the_solar_prediction_in_watts(name):
    panel = _panels(name)["Energy Meters"]
    assert panel["fieldConfig"]["defaults"]["unit"] == "watt"
    query = _target(panel, "Prediction")["query"]
    assert f'from(bucket: "{PREDICTION_BUCKET[name]}")' in query
    assert '"solar_yield_avg_prediction"' in query and "* 3600.0" in query
    _override(panel, "byName", "solar_yield_avg_prediction")


@pytest.mark.parametrize("name", sorted(PREDICTION_BUCKET))
def test_a_bucket_is_picked_by_the_past_part_of_the_range(name):
    """Only the past holds data; with now/d+2d the full range is 3 days."""
    tiered = 0
    for title, panel in _panels(name).items():
        if "targets" not in panel:
            continue
        for target in panel["targets"]:
            if "query" not in target or "windowDuration" not in target["query"]:
                continue
            tiered += 1
            query = target["query"]
            past = "pastStop = if v.timeRangeStop > now() then now() else v.timeRangeStop"
            assert past in query, title
            assert "windowDuration = int(v: pastStop) - int(v: v.timeRangeStart)" in query, title
    assert tiered == 17


@pytest.mark.parametrize(
    "name, hourly",
    [
        ("production_dashboard.json", "network_1hour"),
        ("staging_dashboard.json", "network_1hour_staging"),
    ],
)
def test_network_ping_reads_the_hourly_bucket_for_long_or_old_ranges(name, hourly):
    """Raw ping data is kept 30 days, so a short range from long ago is hourly too."""
    query = _target(_panels(name)["Network Ping"], "A")["query"]
    assert 'from(bucket: "network")' in query and f'from(bucket: "{hourly}")' in query
    use_hours = (
        "useHours = windowDuration > eightDaysNs or "
        "int(v: v.timeRangeStart) < int(v: now()) - int(v: 29d)"
    )
    assert use_hours in query
    assert "rawStart = if useHours then 1970-01-01T00:00:00Z else v.timeRangeStart" in query


def test_production_and_staging_have_the_same_panels_and_queries():
    production, staging = _panels("production_dashboard.json"), _panels("staging_dashboard.json")
    assert production.keys() == staging.keys()
    for title, panel in production.items():
        refs = sorted(t["refId"] for t in panel.get("targets", []))
        assert refs == sorted(t["refId"] for t in staging[title].get("targets", [])), title
