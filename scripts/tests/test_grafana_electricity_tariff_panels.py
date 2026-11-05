"""The electricity dashboard follows the account's current tariff: a panel names it,
the Cheapest Time Window reads its rates, and the Agile Prices panel stays on Agile."""

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = REPO_ROOT / "data" / "grafana" / "electricity-dashboard.json"
REFERENCE = REPO_ROOT / "data" / "grafana" / "mariadb" / "electricity-queries.md"
AGILE_PRICES_ID = 3
CHEAPEST_WINDOW_ID = 13
CURRENT_TARIFF_ID = 22
CURRENT_TARIFF_PRICES_ID = 23
RATE_PRICED_QUERIES = [(1, "A"), (5, "A"), (8, "A"), (14, "A")]
RATE_VALIDITY_BOUND = "c.period_from < COALESCE(pr2.valid_to"
MIDDAY_STANDING_CHARGE = "= '12:00:00' THEN pr.standing_charge END"
AGILE_PRODUCT_PATTERN = "REGEXP '^AGILE-(FLEX-)?[0-9]'"
# Probes the indexed product_rate table, not the actual_rates CTE: MariaDB
# re-evaluates a CTE for every probe, which ran the Pi's database short of memory.
PROJECTION_YIELDS_TO_PUBLISHED_RATE = (
    "NOT EXISTS (\n"
    "      SELECT 1 FROM product_rate pr3\n"
    "      WHERE pr3.product_code = sp.product_code"
)


def _dashboard() -> dict[str, Any]:
    return json.loads(DASHBOARD.read_text(encoding="utf-8"))


def _sql(panel_id: int, ref_id: str = "A") -> str:
    panel = next(p for p in _dashboard()["panels"] if p["id"] == panel_id)
    target = next(t for t in panel["targets"] if t["refId"] == ref_id)
    return target["rawSql"].replace("\r\n", "\n")


def test_the_current_tariff_panel_reads_the_current_electricity_agreement() -> None:
    panel = next(p for p in _dashboard()["panels"] if p["id"] == CURRENT_TARIFF_ID)
    sql = _sql(CURRENT_TARIFF_ID)

    assert panel["title"] == "Current Tariff"
    assert panel["type"] == "stat"
    assert "display_name" in sql
    assert "FROM agreement" in sql
    assert "a.energy = 'E'" in sql
    assert "NOW() < COALESCE(a.valid_to" in sql


def test_the_stat_panel_type_is_required() -> None:
    assert "stat" in {r["id"] for r in _dashboard()["__requires"]}


def test_agile_prices_reads_the_agile_product_not_the_accounts_product() -> None:
    sql = _sql(AGILE_PRICES_ID)

    assert "agile_product" in sql
    assert AGILE_PRODUCT_PATTERN in sql
    assert "FROM agile_forecast" in sql


def test_cheapest_window_uses_the_agile_forecast_only_for_an_agile_product() -> None:
    sql = _sql(CHEAPEST_WINDOW_ID)

    assert f"sp.product_code {AGILE_PRODUCT_PATTERN}" in sql


def test_cheapest_window_expands_rates_to_half_hourly_slots() -> None:
    sql = _sql(CHEAPEST_WINDOW_ID)

    assert sql.startswith("WITH RECURSIVE")
    assert "INTERVAL 30 MINUTE" in sql
    assert "pr2.valid_from <= sp.time" in sql


def test_reference_doc_carries_the_same_queries_as_the_dashboard() -> None:
    reference = REFERENCE.read_text(encoding="utf-8")

    for panel_id in (
        AGILE_PRICES_ID,
        CHEAPEST_WINDOW_ID,
        CURRENT_TARIFF_ID,
        CURRENT_TARIFF_PRICES_ID,
    ):
        assert f"```sql\n{_sql(panel_id)}\n```\n" in reference, panel_id
    for panel_id, ref_id in RATE_PRICED_QUERIES:
        assert _sql(panel_id, ref_id) in reference, (panel_id, ref_id)
    assert "### Current Tariff — stat, id 22" in reference
    assert "### Current Tariff Prices — timeseries, id 23" in reference


def test_every_cost_query_requires_the_rate_row_to_still_cover_the_half_hour() -> None:
    for panel_id, ref_id in RATE_PRICED_QUERIES:
        assert RATE_VALIDITY_BOUND in _sql(panel_id, ref_id), (panel_id, ref_id)


def test_a_days_standing_charge_is_the_one_at_local_midday() -> None:
    for panel_id in (1, 8):
        assert MIDDAY_STANDING_CHARGE in _sql(panel_id), panel_id


def test_cheapest_window_prices_each_slot_by_the_agreement_covering_it() -> None:
    sql = _sql(CHEAPEST_WINDOW_ID)

    assert "slot_products" in sql
    assert "s.time < COALESCE(a.valid_to" in sql
    assert "a.valid_from < COALESCE(a.valid_to" in sql
    assert "sp.time < COALESCE(pr2.valid_to" in sql
    assert f"sp.product_code {AGILE_PRODUCT_PATTERN}" in sql
    assert "current_agreement" not in sql


def test_the_current_tariff_panel_selects_the_text_field_it_shows() -> None:
    panel = next(p for p in _dashboard()["panels"] if p["id"] == CURRENT_TARIFF_ID)

    # An empty selector shows numeric fields only, which would hide the name.
    assert panel["options"]["reduceOptions"]["fields"] == "/^tariff$/"


def test_cheapest_window_only_offers_blocks_whose_every_half_hour_has_a_rate() -> None:
    sql = _sql(CHEAPEST_WINDOW_ID)

    for slots, name in ((2, "avg_1h"), (4, "avg_2h"), (6, "avg_3h"), (8, "avg_4h")):
        assert (
            f"COUNT(rate) OVER (ORDER BY time ROWS BETWEEN CURRENT ROW AND {slots - 1} FOLLOWING) = {slots}"
            in sql
        )
        assert f"time + INTERVAL {(slots - 1) * 30} MINUTE" in sql
        assert f"THEN w.{name}" in sql or f"ELSE w.{name}" in sql
    assert "WHERE bl.avg_rate IS NOT NULL" in sql


def test_cheapest_window_fills_unpublished_half_hours_from_the_tariff_projection() -> (
    None
):
    sql = _sql(CHEAPEST_WINDOW_ID)

    assert "FROM tariff_projection tp" in sql
    assert "tp.product_code = sp.product_code" in sql
    assert PROJECTION_YIELDS_TO_PUBLISHED_RATE in sql
    assert "SELECT time, rate FROM projected_rates" in sql


def _panel(panel_id: int) -> dict[str, Any]:
    return next(p for p in _dashboard()["panels"] if p["id"] == panel_id)


def test_current_tariff_prices_is_a_time_series_at_the_top_of_the_right_column() -> (
    None
):
    panel = _panel(CURRENT_TARIFF_PRICES_ID)

    assert panel["type"] == "timeseries"
    assert panel["title"] == "Current Tariff Prices"
    assert panel["gridPos"] == {"h": 9, "w": 10, "x": 14, "y": 0}
    assert panel["fieldConfig"]["defaults"]["unit"] == "p/kwh"
    thresholds = panel["fieldConfig"]["defaults"]["thresholds"]["steps"]
    assert (
        thresholds
        == _panel(AGILE_PRICES_ID)["fieldConfig"]["defaults"]["thresholds"]["steps"]
    )


def test_current_tariff_prices_take_published_then_projected_then_forecast_rates() -> (
    None
):
    sql = _sql(CURRENT_TARIFF_PRICES_ID)

    assert "slot_products" in sql
    assert "sp.time < COALESCE(pr2.valid_to" in sql
    assert "FROM tariff_projection tp" in sql
    assert f"sp.product_code {AGILE_PRODUCT_PATTERN}" in sql
    assert PROJECTION_YIELDS_TO_PUBLISHED_RATE in sql
    assert (
        "NOT EXISTS (SELECT 1 FROM actual_rates a WHERE a.time = f.period_from)" in sql
    )
    for column in ("published", "projected", "forecast"):
        assert f"AS {column}" in sql
    assert "$__timeFrom()" in sql and "$__timeTo()" in sql


def test_projected_and_forecast_prices_are_dashed_and_every_line_steps() -> None:
    panel = _panel(CURRENT_TARIFF_PRICES_ID)
    defaults = panel["fieldConfig"]["defaults"]["custom"]
    dashed = {
        o["matcher"]["options"]
        for o in panel["fieldConfig"]["overrides"]
        if o["matcher"]["id"] == "byName"
        and any(
            p["id"] == "custom.lineStyle" and p["value"]["fill"] == "dash"
            for p in o["properties"]
        )
    }

    assert defaults["drawStyle"] == "line"
    assert defaults["lineInterpolation"] == "stepAfter"
    assert dashed == {"projected", "forecast"}


def test_the_now_and_plus_24h_markers_show_on_both_price_charts() -> None:
    markers = [
        a for a in _dashboard()["annotations"]["list"] if a["name"] in ("+24h", "0h")
    ]

    assert len(markers) == 2
    for marker in markers:
        assert marker["filter"]["ids"] == [AGILE_PRICES_ID, CURRENT_TARIFF_PRICES_ID]


def test_the_agile_prices_and_cheapest_window_sit_below_the_current_tariff_prices() -> (
    None
):
    assert _panel(AGILE_PRICES_ID)["gridPos"]["y"] == 9
    assert _panel(CHEAPEST_WINDOW_ID)["gridPos"]["y"] == 18


def test_no_two_electricity_panels_overlap() -> None:
    cells: set[tuple[int, int]] = set()
    for panel in _dashboard()["panels"]:
        grid = panel["gridPos"]
        panel_cells = {
            (x, y)
            for x in range(grid["x"], grid["x"] + grid["w"])
            for y in range(grid["y"], grid["y"] + grid["h"])
        }
        assert not cells & panel_cells, panel["title"]
        cells |= panel_cells


def test_every_half_hour_of_the_timeline_is_a_row_so_a_gap_breaks_the_line() -> None:
    # A half-hour with no rate must be an all-null row: dropped instead, its
    # neighbours become adjacent points and the step crosses the gap at the
    # earlier price.
    sql = _sql(CURRENT_TARIFF_PRICES_ID)

    assert "FROM slots s\n  LEFT JOIN sourced_rates r ON r.time = s.time" in sql
    assert "SELECT time, rate, source FROM timeline\n" in sql


def test_each_run_is_closed_by_its_own_row_at_its_last_second() -> None:
    # With stepAfter a series' last point draws nothing, so each run of one
    # source gets a closing row at start + 29:59 in its own column. On its own
    # row, never a half-hour's, so a single unpriced half-hour or a single
    # half-hour of another source still leaves a null between runs. Found in
    # one window pass: a per-row EXISTS over the CTEs re-runs them for every
    # row and ran MariaDB out of memory on the Pi.
    sql = _sql(CURRENT_TARIFF_PRICES_ID)

    assert "LEAD(r.source) OVER (ORDER BY s.time) AS next_source" in sql
    assert "SELECT time + INTERVAL 1799 SECOND, rate, source FROM timeline" in sql
    assert "WHERE source IS NOT NULL AND NOT (next_source <=> source)" in sql
    for column in ("published", "projected", "forecast"):
        assert f"CASE WHEN source = '{column}' THEN rate END AS {column}" in sql
    assert "previous_rate" not in sql
    assert "EXISTS (SELECT 1 FROM projected_rates" not in sql
    assert "EXISTS (SELECT 1 FROM forecast_rates" not in sql


def test_no_projected_slot_is_checked_against_the_actual_rates_cte() -> None:
    for panel_id in (CHEAPEST_WINDOW_ID, CURRENT_TARIFF_PRICES_ID):
        assert "WHERE a.time = tp.period_from" not in _sql(panel_id), panel_id


def test_cheapest_window_reads_its_windows_once() -> None:
    # Twelve scalar subqueries each re-evaluating the windows CTE took about
    # 380 MiB of server memory once five days of Go rates were projected.
    sql = _sql(CHEAPEST_WINDOW_ID)

    assert sql.count("FROM windows") == 1
    assert (
        "ROW_NUMBER() OVER (PARTITION BY bl.window_size, h.horizon ORDER BY bl.avg_rate ASC)"
        in sql
    )
    assert "FROM block_sizes b\nLEFT JOIN cheapest c" in sql


def test_current_tariff_prices_cover_at_most_the_last_seven_days_of_the_range() -> None:
    # One slot per half-hour: a wide dashboard range would pass MariaDB's
    # recursion limit and build the per-slot subqueries on a memory-capped Pi.
    sql = _sql(CURRENT_TARIFF_PRICES_ID)

    assert "GREATEST($__timeFrom(), $__timeTo() - INTERVAL 7 DAY)" in sql
