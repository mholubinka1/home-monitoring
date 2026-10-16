import json
import re
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


def _gas_panels() -> list[dict]:
    return _panels(_load(GAS_HEATING_DASHBOARD))


def test_every_time_series_query_returns_a_time_column_for_the_join() -> None:
    for panel in _gas_panels():
        if panel["type"] != "timeseries":
            continue
        for target in panel["targets"]:
            assert target["format"] == "table", (panel["title"], target["refId"])
            assert re.search(
                r"\bAS time\b|\$__timeGroupAlias\(|^SELECT time\b",
                target["rawSql"],
                re.MULTILINE,
            ), (panel["title"], target["refId"])


def test_every_series_override_names_a_column_alias_of_its_query() -> None:
    # The colours, axes and units are by-name overrides, so a renamed alias
    # in the SQL would silently drop them.
    for panel in _gas_panels():
        sql = " ".join(t["rawSql"] for t in panel["targets"])
        for override in panel["fieldConfig"]["overrides"]:
            series = override["matcher"]["options"]
            assert re.search(rf"\bAS {series}\b", sql), (panel["title"], series)


def test_the_current_location_tie_breaks_on_the_newest_row() -> None:
    # After a location change two places can share the newest hour.
    outdoor = next(t for t in _panel(TEMPERATURES)["targets"] if t["refId"] == "B")

    assert "ORDER BY l.observed_at DESC, l.id DESC LIMIT 1" in outdoor["rawSql"]


BUILT_IN_VARIABLES = {"DS_MYSQL", "__interval", "__interval_ms", "__from", "__to"}


def test_every_variable_the_panels_use_is_defined() -> None:
    dashboard = _load(GAS_HEATING_DASHBOARD)
    defined = {v["name"] for v in dashboard["templating"]["list"]} | BUILT_IN_VARIABLES
    text = json.dumps(dashboard["panels"]) + json.dumps(
        [v.get("query") for v in dashboard["templating"]["list"]]
    )

    used = set(re.findall(r"\$\{(\w+)(?::\w+)?\}", text))
    used |= set(re.findall(r"\$([A-Za-z]\w*)", text))
    assert used - defined == set()


def test_the_dashboard_range_reaches_a_week_ahead_for_the_forecast() -> None:
    assert _load(GAS_HEATING_DASHBOARD)["time"]["to"] == "now+7d"


def test_the_agreed_defaults_are_set() -> None:
    variables = {
        v["name"]: v for v in _load(GAS_HEATING_DASHBOARD)["templating"]["list"]
    }

    assert variables["boiler_kw"]["query"] == "12"
    assert variables["comfort_target"]["query"] == "15"
    assert variables["comfort_margin"]["query"] == "1.0"
    assert variables["gas_unit"]["query"] == "kWh,pounds"


def test_daily_gas_panels_use_only_complete_gas_days() -> None:
    # A stored gas total of zero means missing, and a short reading count
    # means a partial day: neither may be drawn.
    daily = [
        t["rawSql"]
        for p in _gas_panels()
        for t in p["targets"]
        if "daily_consumption_summary" in t["rawSql"]
    ]

    assert len(daily) == 4  # kWh, pounds, monthly, heatmap
    for sql in daily:
        assert "s.total_kwh > 0" in sql
        assert "s.half_hour_count = TIMESTAMPDIFF(" in sql


def test_the_predictor_reads_the_gas_row_of_the_cost_forecast() -> None:
    predictor = next(p for p in _gas_panels() if p["type"] == "bargauge")

    assert "FROM cost_forecast" in predictor["targets"][0]["rawSql"]
    assert "WHERE energy = 'G'" in predictor["targets"][0]["rawSql"]


def test_boost_cost_is_minutes_over_sixty_times_kw_times_the_unit_rate() -> None:
    boost = next(p for p in _gas_panels() if p["title"].startswith("Boost Minutes"))
    sql = boost["targets"][0]["rawSql"]

    assert "boost_minutes / 60 *" in sql
    assert "COUNT(CASE WHEN boost_active = 1 THEN 1 END) * 2 AS boost_minutes" in sql
    assert "/ 100, 4)" in sql
    overrides = {
        o["matcher"]["options"]: {p["id"]: p["value"] for p in o["properties"]}
        for o in boost["fieldConfig"]["overrides"]
    }
    assert (
        "estimated; assumes full output" in overrides["extra_cost_gbp"]["displayName"]
    )


def test_a_boiler_input_that_is_zero_or_not_a_number_shows_minutes_only() -> None:
    boost = next(p for p in _gas_panels() if p["title"].startswith("Boost Minutes"))
    sql = boost["targets"][0]["rawSql"]

    assert "REGEXP '^[0-9]+([.][0-9]+)?$'" in sql
    assert ") > 0," in sql
    assert "      NULL\n    ) AS extra_cost_gbp" in sql


def test_text_box_values_enter_the_sql_escaped() -> None:
    # A dashboard text box can be set from the URL, so it is never pasted
    # between hand-written quotes.
    sql = " ".join(t["rawSql"] for p in _gas_panels() for t in p["targets"])

    for name in ("boiler_kw", "comfort_target", "comfort_margin", "gas_unit", "region"):
        assert f"'${{{name}}}'" not in sql, name
        assert f"${{{name}:sqlstring}}" in sql, name


def test_a_missing_day_is_a_gap_on_the_daily_temperature_lines() -> None:
    outdoor = _panel_overrides("Daily Gas and Outdoor Temperature (${gas_unit})")[
        "outdoor_mean"
    ]
    assert outdoor["custom.spanNulls"] is False
    assert outdoor["custom.insertNulls"] == 129600000

    band = _panel("Daily Outdoor Temperature Band with Sunshine")
    custom = band["fieldConfig"]["defaults"]["custom"]
    assert custom["spanNulls"] is False
    assert custom["insertNulls"] == 129600000


def test_a_comfort_setting_that_is_not_a_number_shows_no_data_not_zero() -> None:
    below = next(p for p in _gas_panels() if p["title"].startswith("Minutes Below"))
    sql = below["targets"][0]["rawSql"]

    assert "CAST(${comfort_target:sqlstring} AS DECIMAL(8, 2)), NULL)" in sql
    assert "IS NULL OR" in sql


def test_the_band_shades_between_its_maximum_and_minimum_display_names() -> None:
    overrides = _panel_overrides("Daily Outdoor Temperature Band with Sunshine")

    assert (
        overrides["max_temp"]["custom.fillBelowTo"]
        == overrides["min_temp"]["displayName"]
    )


def test_the_outdoor_complete_day_rule_is_the_same_in_every_copy() -> None:
    # The daily gas panel's temperature line and the band both keep a London
    # day only when every hour of it has a reading (23, 24 or 25).
    rule = (
        "HAVING COUNT(*) = TIMESTAMPDIFF(\n    HOUR,\n"
        "    CONVERT_TZ(CAST(day AS DATETIME), '${timezone}', 'UTC'),"
    )
    copies = [
        t["rawSql"]
        for p in _gas_panels()
        for t in p["targets"]
        if "weather_observation" in t["rawSql"] and "wx AS" in t["rawSql"]
    ]

    assert len(copies) == 2
    for sql in copies:
        assert rule in sql
        assert "ORDER BY l.observed_at DESC, l.id DESC LIMIT 1" in sql
