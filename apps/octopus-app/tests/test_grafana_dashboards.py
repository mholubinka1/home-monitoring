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
    # bar gauge, the one non-chart exception.
    types = {p["type"] for p in _panels(_load(GAS_HEATING_DASHBOARD))}

    assert types
    assert types <= {"timeseries", "heatmap", "bargauge", "xychart"}
    assert [p["type"] for p in _panels(_load(GAS_HEATING_DASHBOARD))].count(
        "bargauge"
    ) == 1


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
    overrides = _panel_overrides("Daily Gas and Outdoor Temperature (${gas_unit})")
    assert overrides["outdoor_mean"]["custom.spanNulls"] is False
    assert overrides["outdoor_mean"]["custom.insertNulls"] == 129600000

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


def test_the_monthly_and_heatmap_panels_carry_their_own_time_ranges() -> None:
    # Neither query uses the dashboard range: the monthly one needs 13+ months
    # of history, the heatmap is "today" with its hours on the right day.
    monthly = next(p for p in _gas_panels() if p["title"].startswith("Monthly Gas"))
    heatmap = next(p for p in _gas_panels() if p["type"] == "heatmap")

    assert monthly["timeFrom"] == "400d"
    assert heatmap["timeFrom"] == "now/d"
    assert heatmap["hideTimeOverride"] is True


def test_minutes_and_days_use_a_fixed_suffix_not_an_auto_scaling_unit() -> None:
    for title, series in (
        ("Boost Minutes and Estimated Extra Gas Cost", "boost_minutes"),
        ("Daily Outdoor Temperature Band with Sunshine", "sunshine_hours"),
    ):
        assert _panel_overrides(title)[series]["unit"].startswith("suffix: ")


def test_every_copy_of_a_shared_fragment_keeps_all_its_parts() -> None:
    queries = [t["rawSql"] for p in _gas_panels() for t in p["targets"]]

    gas_days = [q for q in queries if "daily_consumption_summary" in q]
    for sql in gas_days:
        assert "s.half_hour_count IS NULL" in sql  # days before the API's first day

    ranked = [q for q in queries if "ROW_NUMBER() OVER" in q]
    assert len(ranked) == 3  # panel 1 outdoor, daily gas C, the band
    for sql in ranked:
        assert "ORDER BY (COALESCE(w.source, '') = 'open-meteo-archive'), w.id" in sql

    comfort = next(q for q in queries if "minutes_below_target" in q)
    for name in ("comfort_target", "comfort_margin"):
        assert f"${{{name}:sqlstring}} REGEXP" in comfort


def _query(title: str, ref_id: str) -> str:
    return next(t for t in _panel(title)["targets"] if t["refId"] == ref_id)["rawSql"]


def test_the_forecast_outdoor_line_continues_outdoor() -> None:
    sql = _query(TEMPERATURES, "C")
    overrides = _panel_overrides(TEMPERATURES)

    assert "AS outdoor_forecast" in sql
    assert "FROM weather_forecast" in sql
    assert overrides["outdoor_forecast"]["custom.lineStyle"]["fill"] == "dash"
    assert overrides["outdoor_forecast"]["color"] == overrides["outdoor"]["color"]


def test_a_forecast_day_is_hidden_once_an_outdoor_reading_covers_it() -> None:
    # Actuals overwrite the forecast: only forecast stamps later than the
    # newest outdoor reading draw, and one join point at that reading keeps the
    # dashed line attached to the solid one.
    sql = _query(TEMPERATURES, "C")

    assert "WITH latest AS (" in sql
    assert "> latest.observed_at" in sql
    assert "UNION ALL" in sql
    assert "f.target_date >= DATE(CONVERT_TZ(UTC_TIMESTAMP()" in sql
    # The newest fetch wins when a day has several rows; the id is text.
    assert "ORDER BY f2.fetched_at DESC, f2.id DESC LIMIT 1" in sql
    assert "MAX(f2.id)" not in sql


def test_the_daily_gas_panel_no_longer_carries_the_forecast() -> None:
    panel = _panel("Daily Gas and Outdoor Temperature (${gas_unit})")

    assert "weather_forecast" not in " ".join(t["rawSql"] for t in panel["targets"])
    assert "forecast_mean" not in json.dumps(panel)
    assert "forecast" not in panel["description"].lower()


def test_monthly_gas_is_a_year_on_year_percentage_without_day_counts() -> None:
    panel = next(p for p in _gas_panels() if p["title"].startswith("Monthly Gas"))
    sql = panel["targets"][0]["rawSql"]
    series = [o["matcher"]["options"] for o in panel["fieldConfig"]["overrides"]]

    assert "AS yoy_pct_change" in sql
    assert "DAY(LAST_DAY(" in sql  # still scaled to the month's full days
    assert "days_counted" not in sql
    assert "days_counted" not in json.dumps(panel["fieldConfig"])
    assert series == ["yoy_pct_change"]
    # Threshold colours only colour each bar by its own value in scheme mode.
    custom = panel["fieldConfig"]["defaults"]["custom"]
    assert custom["gradientMode"] == "scheme"
    # A line, like the electricity year-on-year panel, not bars.
    assert custom["drawStyle"] == "line"
    # One quiet month a year ago must not flatten the other eleven.
    defaults = panel["fieldConfig"]["defaults"]
    assert (defaults["min"], defaults["max"]) == (-100, 100)
    assert "t.month_start - INTERVAL 12 MONTH" in sql  # the same month a year ago
    assert "HAVING COUNT(*) >= 10" in sql
    steps = panel["fieldConfig"]["defaults"]["thresholds"]["steps"]
    assert [(s["color"], s["value"]) for s in steps] == [("green", None), ("red", 0)]
    assert panel["fieldConfig"]["defaults"]["unit"] == "percent"


MODEL_CHART_PREFIX = "Heating Model"
LATEST_USABLE_MODEL = (
    "SELECT m.id FROM heating_model m WHERE m.usable = 1 ORDER BY m.id DESC LIMIT 1"
)


def _model_chart() -> dict:
    return next(p for p in _gas_panels() if p["title"].startswith(MODEL_CHART_PREFIX))


def test_the_model_chart_is_an_xy_chart_of_gas_against_effective_temperature() -> None:
    panel = _model_chart()

    assert panel["type"] == "xychart"
    dots = panel["targets"][0]["rawSql"]
    assert "AS effective_temp" in dots
    assert "AS gas_kwh" in dots
    assert "FROM heating_day_verdict" in dots
    overrides = {
        o["matcher"]["options"]: {p["id"]: p["value"] for p in o["properties"]}
        for o in panel["fieldConfig"]["overrides"]
    }
    assert overrides["gas_kwh"]["custom.show"] == "points"
    assert overrides["fitted_kwh"]["custom.show"] == "lines"


def test_the_model_chart_reads_only_days_judged_by_the_latest_usable_model() -> None:
    dots, line = (t["rawSql"] for t in _model_chart()["targets"][:2])

    assert f"d.model_id = ({LATEST_USABLE_MODEL})" in dots
    assert "d.away = 0" in dots  # an away day says nothing about the house
    assert f"m.id = ({LATEST_USABLE_MODEL})" in line


def test_the_fitted_line_is_baseload_plus_slope_below_the_threshold() -> None:
    line = _model_chart()["targets"][1]["rawSql"]

    assert "baseload + slope * GREATEST(0, threshold - x)" in line
    assert "AS fitted_kwh" in line
    assert line.count("UNION ALL") == 2  # the cold end, the kink, the warm end


def test_the_model_chart_title_carries_the_fit_and_its_label() -> None:
    dashboard = _load(GAS_HEATING_DASHBOARD)
    variable = next(
        v for v in dashboard["templating"]["list"] if v["name"] == "model_summary"
    )

    assert "${model_summary}" in _model_chart()["title"]
    assert variable["hide"] == 2
    query = variable["query"]
    assert "m.usable = 1" in query
    assert "ORDER BY m.id DESC" in query
    for part in ("m.baseload", "m.threshold", "m.slope", "m.r_squared", "m.label"):
        assert part in query
    assert "m.fitted_at" in query
    assert "no usable model yet" in query


def _rate_lookup(sql: str) -> str:
    start = sql.index("JOIN agreement a")
    return sql[start : sql.index("WHERE ${gas_unit:sqlstring}", start)]


def _expected_queries() -> dict[str, str]:
    targets = _panel("Daily Gas and Outdoor Temperature (${gas_unit})")["targets"]
    return {
        t["refId"]: t["rawSql"]
        for t in targets
        if "heating_forecast_day" in t["rawSql"]
    }


def test_expected_gas_bars_come_from_the_forecast_days_of_the_latest_usable_model() -> (
    None
):
    queries = _expected_queries()

    assert len(queries) == 2  # kWh and pounds, like the actual bars
    for sql in queries.values():
        assert f"f.model_id = ({LATEST_USABLE_MODEL})" in sql
        assert "f.date >= DATE(CONVERT_TZ(UTC_TIMESTAMP()" in sql  # no stale past days
    kwh, pounds = sorted(queries.values(), key=lambda q: "unit_rate" in q)
    assert "AS expected_kwh" in kwh
    assert "${gas_unit:sqlstring} = 'kWh'" in kwh
    assert "${gas_unit:sqlstring} = 'pounds'" in pounds
    assert "f.expected_gas * pr.unit_rate / 100" in pounds
    assert "AS expected_unit_cost_gbp" in pounds
    assert "AS expected_standing_charge_gbp" in pounds
    # Expected pounds are priced exactly as the actual ones are (query B).
    actual = _query("Daily Gas and Outdoor Temperature (${gas_unit})", "B")
    assert _rate_lookup(pounds) == _rate_lookup(actual).replace("s.date", "f.date")


def test_expected_gas_bars_are_lighter_and_labelled_expected() -> None:
    overrides = _panel_overrides("Daily Gas and Outdoor Temperature (${gas_unit})")

    for series, actual in (
        ("expected_kwh", "gas_kwh"),
        ("expected_unit_cost_gbp", "unit_cost_gbp"),
        ("expected_standing_charge_gbp", "standing_charge_gbp"),
    ):
        expected = overrides[series]
        assert expected["custom.fillOpacity"] < 70, series
        assert expected["displayName"].startswith("expected"), series
        assert expected["unit"] == overrides[actual]["unit"], series
        assert expected["color"] == overrides[actual]["color"], series
    for series in ("expected_unit_cost_gbp", "expected_standing_charge_gbp"):
        assert overrides[series]["custom.stacking"]["mode"] == "normal"
    for series in ("expected_kwh", "expected_unit_cost_gbp"):
        assert overrides[series]["displayName"].endswith("(estimated)"), series
