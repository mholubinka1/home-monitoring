"""The Grafana day panels take the local-day timezone from a dashboard variable,
so they follow the same setting as the apps (ADR-0010)."""

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = REPO_ROOT / "data" / "grafana" / "dashboard.json"
REFERENCE_QUERIES = REPO_ROOT / "data" / "grafana" / "mariadb" / "queries.md"
DEFAULT_TIMEZONE = "Europe/London"


def _variables() -> dict[str, dict[str, Any]]:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    return {item["name"]: item for item in dashboard["templating"]["list"]}


def test_the_dashboard_has_a_timezone_variable_defaulting_to_london() -> None:
    variable = _variables()["timezone"]

    assert variable["type"] == "constant"
    assert variable["query"] == DEFAULT_TIMEZONE


def _raw_queries(node: Any) -> list[str]:
    """Every rawSql string in the dashboard, wherever panels nest them."""
    if isinstance(node, dict):
        found = [node["rawSql"]] if isinstance(node.get("rawSql"), str) else []
        return found + [q for value in node.values() for q in _raw_queries(value)]
    if isinstance(node, list):
        return [q for item in node for q in _raw_queries(item)]
    return []


def test_no_dashboard_query_hard_codes_a_timezone() -> None:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    queries = _raw_queries(dashboard["panels"])

    assert queries, "no panel queries found"
    assert [q for q in queries if f"'{DEFAULT_TIMEZONE}'" in q] == []
    assert any("'${timezone}'" in q for q in queries)


def test_the_reference_queries_use_the_timezone_variable_not_a_literal() -> None:
    text = REFERENCE_QUERIES.read_text(encoding="utf-8")

    assert f"'{DEFAULT_TIMEZONE}'" not in text
    assert "'${timezone}'" in text
