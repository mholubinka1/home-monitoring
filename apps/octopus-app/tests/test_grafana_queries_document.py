"""The gas and heating dashboard's queries and the queries document agree, and no
panel reads a retired table (ADR-0036)."""

import json
from pathlib import Path

import pytest

GRAFANA_DIR = Path(__file__).resolve().parents[3] / "data" / "grafana"
DASHBOARD = GRAFANA_DIR / "gas-heating-dashboard.json"
QUERIES_DOCUMENT = GRAFANA_DIR / "mariadb" / "gas-heating-queries.md"
HEATING_COST = "Heating Cost per Half-Hour"
COST_COMPARISON = "Daily Cost: Schedules vs Actual"
TEMPERATURES = "Indoor, Target and Outdoor Temperature"
PLAN_PANEL = "Heating Plan and Room Model"


def _all_panels() -> list[dict]:
    panels: list[dict] = []
    pending = json.loads(DASHBOARD.read_text(encoding="utf-8"))["panels"]
    while pending:
        panel = pending.pop()
        panels.append(panel)
        pending.extend(panel.get("panels", []))
    return panels


def _panel(title: str) -> dict:
    return next(panel for panel in _all_panels() if panel.get("title") == title)


@pytest.mark.parametrize(
    ("title", "ref"),
    [
        (HEATING_COST, "A"),
        (COST_COMPARISON, "A"),
        (TEMPERATURES, "E"),
        (PLAN_PANEL, "A"),
        (PLAN_PANEL, "D"),
        (PLAN_PANEL, "E"),
        ("Schedule Today", "A"),
    ],
)
def test_a_changed_panels_query_is_the_one_in_the_queries_document(
    title: str, ref: str
) -> None:
    target = next(t for t in _panel(title)["targets"] if t["refId"] == ref)

    assert target["rawSql"].strip() in QUERIES_DOCUMENT.read_text(encoding="utf-8")


def test_no_panel_reads_a_retired_hourly_table() -> None:
    sql = " ".join(
        target.get("rawSql", "")
        for panel in _all_panels()
        for target in panel.get("targets", [])
    )

    assert "room_forecast_hour" not in sql
    assert "heating_hour" not in sql


def test_the_queries_document_lists_the_retired_tables_and_how_to_drop_them() -> None:
    document = QUERIES_DOCUMENT.read_text(encoding="utf-8")

    retired = document[document.index("## Retired tables") :]
    assert "DROP TABLE heating_hour;" in retired
    assert "DROP TABLE room_forecast_hour;" in retired


def test_the_presence_panels_query_is_the_one_in_the_queries_document() -> None:
    target = _panel("Presence")["targets"][0]
    document = QUERIES_DOCUMENT.read_text(encoding="utf-8")

    assert target["rawSql"].strip() in document
    assert "### Presence (state timeline)" in document


def test_the_queries_document_lists_the_presence_period_table_in_its_schema() -> None:
    document = QUERIES_DOCUMENT.read_text(encoding="utf-8")

    schema = document[document.index("## Schema assumed") : document.index("---")]
    assert "presence_period" in schema
    assert "no_one_home" in schema
