import json
from pathlib import Path

import pytest

GRAFANA_DIR = Path(__file__).resolve().parents[3] / "data" / "grafana"
DASHBOARD_FILES = sorted(GRAFANA_DIR.glob("*.json"))
GAS_HEATING_DASHBOARD = GRAFANA_DIR / "gas-heating-dashboard.json"
TEMPERATURES = "Indoor, Target and Outdoor Temperature"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _panels(dashboard: dict) -> list[dict]:
    return dashboard["panels"]


def test_every_dashboard_file_has_a_distinct_title_and_uid() -> None:
    dashboards = [_load(path) for path in DASHBOARD_FILES]

    assert len(dashboards) >= 2
    titles = [d["title"] for d in dashboards]
    uids = [d["uid"] for d in dashboards]
    assert len(set(titles)) == len(titles)
    assert len(set(uids)) == len(uids)


def test_panel_ids_are_unique_within_the_gas_heating_dashboard() -> None:
    ids = [p["id"] for p in _panels(_load(GAS_HEATING_DASHBOARD))]

    assert len(set(ids)) == len(ids)


def test_the_gas_heating_dashboard_has_only_chart_panels() -> None:
    # House style: every panel is a chart. The billing-period predictor is a
    # bar gauge (planned), the one non-chart exception.
    types = {p["type"] for p in _panels(_load(GAS_HEATING_DASHBOARD))}

    assert types
    assert types <= {"timeseries", "barchart", "heatmap", "bargauge"}


@pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
def test_every_target_names_the_home_monitoring_dataset(path: Path) -> None:
    # The data source's Database field is what Grafana queries; "dataset" must
    # still name the same database (issue #584).
    for panel in _panels(_load(path)):
        for target in panel.get("targets", []):
            assert target["dataset"] == "home_monitoring", panel["title"]


@pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
def test_every_datasource_is_the_importable_placeholder(path: Path) -> None:
    # A hard-coded data source uid breaks on any Grafana without that uid.
    for panel in _panels(_load(path)):
        assert panel.get("datasource", {}).get("uid") == "${DS_MYSQL}", panel["title"]
        for target in panel.get("targets", []):
            assert target["datasource"]["uid"] == "${DS_MYSQL}", panel["title"]


def test_the_gas_heating_dashboard_uses_london_time() -> None:
    dashboard = _load(GAS_HEATING_DASHBOARD)

    variables = {v["name"]: v for v in dashboard["templating"]["list"]}
    assert dashboard["timezone"] == "Europe/London"
    assert variables["timezone"]["query"] == "Europe/London"
    assert variables["timezone"]["current"]["value"] == "Europe/London"


def _panel(title: str) -> dict:
    return next(p for p in _panels(_load(GAS_HEATING_DASHBOARD)) if p["title"] == title)


def _panel_overrides(title: str) -> dict[str, dict[str, object]]:
    return {
        o["matcher"]["options"]: {p["id"]: p["value"] for p in o["properties"]}
        for o in _panel(title)["fieldConfig"]["overrides"]
    }


def test_the_target_temperature_is_drawn_as_a_step() -> None:
    overrides = _panel_overrides(TEMPERATURES)

    assert overrides["target"]["custom.lineInterpolation"] == "stepAfter"
    assert "custom.lineInterpolation" not in overrides["indoor"]
    assert "custom.lineInterpolation" not in overrides["outdoor"]


def test_lines_join_across_missing_points_so_the_hourly_outdoor_line_draws() -> None:
    custom = _panel(TEMPERATURES)["fieldConfig"]["defaults"]["custom"]

    assert custom["spanNulls"] is True
    for series, properties in _panel_overrides(TEMPERATURES).items():
        assert "custom.spanNulls" not in properties, series


def test_each_temperature_query_returns_a_time_column_for_the_join() -> None:
    for target in _panel(TEMPERATURES)["targets"]:
        assert target["format"] == "table", target["refId"]
        assert (
            " AS time" in target["rawSql"] or "$__timeGroupAlias(" in target["rawSql"]
        )


def test_every_series_override_names_a_column_alias_of_its_query() -> None:
    # The colours and the step are by-name overrides, so a renamed alias in
    # the SQL would silently drop them.
    sql = " ".join(t["rawSql"] for t in _panel(TEMPERATURES)["targets"])

    for series in _panel_overrides(TEMPERATURES):
        assert re.search(rf"\bAS {series}\b", sql), series
