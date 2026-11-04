"""The electricity dashboard also shows the hypervolt-agile fuel price limit, so it
can be read next to the Agile prices without opening a second dashboard."""

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = REPO_ROOT / "data" / "grafana" / "electricity-dashboard.json"
AGILE_PRICES_ID = 3
FUEL_TABLE = "fuel_price_threshold"
EXISTING_TITLES = [
    "Yesterday's Cost (Electricity)",
    "Billing Period Progress ($billing_period_start → $billing_period_end)${rates_estimated_note}",
    "Latest Consumption",
    "Agile Prices: Today/Tomorrow (Actual + Forecast)",
    "Load Shift Efficiency",
    "Average Consumption Per Day",
    "Consumption Heatmap (Hour-by-Hour, Last 45 Days)",
    "Monthly Total Consumption",
    "Consumption Year-on-Year Percentage Change",
    "Daily Average Unit Price (Rolling 7-Day Window)",
    "Daily Average Cost (Rolling 7-Day Window)",
    "Daily Average Usage (Rolling 7-Day Window)",
]


def _dashboard() -> dict[str, Any]:
    return json.loads(DASHBOARD.read_text(encoding="utf-8"))


def _panel(panel_id: int) -> dict[str, Any]:
    return next(p for p in _dashboard()["panels"] if p["id"] == panel_id)


def test_agile_prices_reads_the_effective_charging_limit_as_its_second_query() -> None:
    targets = {t["refId"]: t for t in _panel(AGILE_PRICES_ID)["targets"]}

    assert FUEL_TABLE in targets["B"]["rawSql"]
    assert "$__timeFrom()" in targets["B"]["rawSql"]
    assert "$__timeTo()" in targets["B"]["rawSql"]


def test_panel_ids_are_unique_and_agile_prices_keeps_id_3() -> None:
    ids = [p["id"] for p in _dashboard()["panels"]]

    assert len(ids) == len(set(ids))
    assert _panel(AGILE_PRICES_ID)["title"].startswith("Agile Prices")


def test_query_b_is_named_effective_charging_limit() -> None:
    overrides = _panel(AGILE_PRICES_ID)["fieldConfig"]["overrides"]
    query_b = next(
        o for o in overrides if o["matcher"] == {"id": "byFrameRefID", "options": "B"}
    )
    properties = {p["id"]: p["value"] for p in query_b["properties"]}

    assert properties["displayName"] == "Effective charging limit"
    assert properties["custom.lineStyle"]["fill"] == "dash"
    assert properties["color"]["fixedColor"] == "semi-dark-purple"
    assert properties["custom.hideFrom"]["legend"] is True
    assert properties["custom.hideFrom"]["tooltip"] is True


def test_fuel_gauge_and_chart_read_the_fuel_price_threshold() -> None:
    gauge, chart = _panel(20), _panel(21)

    assert (gauge["type"], gauge["title"]) == ("gauge", "Fuel price (latest)")
    assert chart["title"] == "Fuel price vs effective charging limit"
    defaults = gauge["fieldConfig"]["defaults"]
    assert (defaults["min"], defaults["max"]) == (100, 200)
    steps = defaults["thresholds"]["steps"]
    assert [s["value"] for s in steps] == [None, 140, 155, 170]
    assert chart["timeFrom"] == "30d"
    for override in chart["fieldConfig"]["overrides"]:
        assert override["matcher"]["options"] in chart["targets"][0]["rawSql"]
    for panel in (gauge, chart):
        assert all(FUEL_TABLE in t["rawSql"] for t in panel["targets"])


def test_panels_do_not_overlap_or_exceed_24_columns() -> None:
    boxes = [p["gridPos"] for p in _dashboard()["panels"]]

    assert all(b["x"] + b["w"] <= 24 for b in boxes)
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            apart = (
                a["x"] + a["w"] <= b["x"]
                or b["x"] + b["w"] <= a["x"]
                or a["y"] + a["h"] <= b["y"]
                or b["y"] + b["h"] <= a["y"]
            )
            assert apart, (a, b)


def test_existing_panels_remain_and_gauge_is_required() -> None:
    dashboard = _dashboard()
    titles = {p.get("title") for p in dashboard["panels"]}

    assert set(EXISTING_TITLES) <= titles
    assert "gauge" in {r["id"] for r in dashboard["__requires"]}
    assert "region" in {v["name"] for v in dashboard["templating"]["list"]}
    marker_annotations = [a for a in dashboard["annotations"]["list"] if "filter" in a]
    assert marker_annotations
    assert all(a["filter"]["ids"] == [AGILE_PRICES_ID] for a in marker_annotations)


def test_reference_queries_document_the_fuel_panels_and_the_limit_query() -> None:
    reference = (
        REPO_ROOT / "data" / "grafana" / "mariadb" / "electricity-queries.md"
    ).read_text(encoding="utf-8")

    assert "### Fuel price (latest) — gauge, id 20" in reference
    assert "### Fuel price vs effective charging limit — timeseries, id 21" in reference
    assert "Query B draws the **Effective charging limit**" in reference
