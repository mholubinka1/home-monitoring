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
RATE_PRICED_QUERIES = [(1, "A"), (4, "B"), (5, "A"), (8, "A"), (14, "A")]
RATE_VALIDITY_BOUND = "c.period_from < COALESCE(pr2.valid_to"
MIDDAY_STANDING_CHARGE = "= '12:00:00' THEN pr.standing_charge END"
AGILE_PRODUCT_PATTERN = "REGEXP '^AGILE-(FLEX-)?[0-9]'"


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


def test_the_current_tariff_panel_is_the_top_banner() -> None:
    panels = _dashboard()["panels"]
    banner = next(p for p in panels if p["id"] == CURRENT_TARIFF_ID)["gridPos"]

    assert banner["y"] == 0 and banner["w"] == 24
    others = [p for p in panels if p["id"] != CURRENT_TARIFF_ID]
    assert all(p["gridPos"]["y"] >= banner["h"] for p in others)


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

    for panel_id in (AGILE_PRICES_ID, CHEAPEST_WINDOW_ID, CURRENT_TARIFF_ID):
        assert _sql(panel_id) in reference
    for panel_id, ref_id in RATE_PRICED_QUERIES:
        assert _sql(panel_id, ref_id) in reference, (panel_id, ref_id)
    assert "### Current Tariff — stat, id 22" in reference


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
    assert "sp.time < COALESCE(pr2.valid_to" in sql
    assert f"sp.product_code {AGILE_PRODUCT_PATTERN}" in sql
    assert "current_agreement" not in sql
