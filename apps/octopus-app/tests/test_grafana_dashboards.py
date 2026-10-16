import json
from pathlib import Path

import pytest

GRAFANA_DIR = Path(__file__).resolve().parents[3] / "data" / "grafana"
DASHBOARD_FILES = sorted(GRAFANA_DIR.glob("*dashboard.json"))
GAS_HEATING_DASHBOARD = GRAFANA_DIR / "gas-heating-dashboard.json"


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
    # bar gauge, the one non-chart exception.
    types = {p["type"] for p in _panels(_load(GAS_HEATING_DASHBOARD))}

    assert types <= {"timeseries", "barchart", "heatmap", "bargauge"}


@pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
def test_every_query_runs_against_the_home_monitoring_database(path: Path) -> None:
    # The data source's Database field is what Grafana queries; "dataset" must
    # still name the same database (issue #584).
    for panel in _panels(_load(path)):
        for target in panel.get("targets", []):
            assert target["dataset"] == "home_monitoring", panel["title"]


@pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
def test_every_datasource_is_the_importable_placeholder(path: Path) -> None:
    # A hard-coded data source uid breaks on any Grafana without that uid.
    for panel in _panels(_load(path)):
        assert panel["datasource"]["uid"] == "${DS_MYSQL}", panel["title"]
        for target in panel.get("targets", []):
            assert target["datasource"]["uid"] == "${DS_MYSQL}", panel["title"]


def test_the_gas_heating_dashboard_uses_london_time() -> None:
    dashboard = _load(GAS_HEATING_DASHBOARD)

    variables = {v["name"]: v for v in dashboard["templating"]["list"]}
    assert dashboard["timezone"] == "Europe/London"
    assert variables["timezone"]["query"] == "Europe/London"
