"""The Grafana day panels take the local-day timezone from a dashboard variable,
so they follow the same setting as the apps (ADR-0010)."""

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = REPO_ROOT / "data" / "grafana" / "electricity-dashboard.json"
REFERENCE_QUERIES = (
    REPO_ROOT / "data" / "grafana" / "mariadb" / "electricity-queries.md"
)
DEFAULT_TIMEZONE = "Europe/London"


# Grafana's "export for sharing externally" turns a constant variable into an
# import input: the variable reads ${VAR_TIMEZONE}, and the import prompt
# offers the input's value as the default.
TIMEZONE_INPUT = "VAR_TIMEZONE"


def _variables() -> dict[str, dict[str, Any]]:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    return {item["name"]: item for item in dashboard["templating"]["list"]}


def _timezone_input() -> dict[str, Any]:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    return next(i for i in dashboard["__inputs"] if i["name"] == TIMEZONE_INPUT)


def test_the_dashboard_has_a_timezone_variable_defaulting_to_london() -> None:
    variable = _variables()["timezone"]

    assert variable["type"] == "constant"
    assert variable["query"] == "${" + TIMEZONE_INPUT + "}"
    assert _timezone_input()["type"] == "constant"
    assert _timezone_input()["value"] == DEFAULT_TIMEZONE


def _strings(node: Any) -> list[str]:
    """Every string anywhere in a parsed JSON document."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for value in node.values() for s in _strings(value)]
    if isinstance(node, list):
        return [s for item in node for s in _strings(item)]
    return []


def _convert_tz_zones(text: str) -> list[set[str]]:
    """The two zone arguments of every CONVERT_TZ call in the text."""
    pattern = r"CONVERT_TZ\(.*?,\s*'([^']+)',\s*'([^']+)'\)"
    return [set(zones) for zones in re.findall(pattern, text, flags=re.DOTALL)]


def _dashboard_without_the_timezone_variable() -> dict[str, Any]:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    variables = dashboard["templating"]["list"]
    dashboard["templating"]["list"] = [v for v in variables if v["name"] != "timezone"]
    inputs = dashboard["__inputs"]
    dashboard["__inputs"] = [i for i in inputs if i["name"] != TIMEZONE_INPUT]
    return dashboard


def test_the_dashboard_names_its_timezone_only_in_the_variable_input() -> None:
    strings = _strings(_dashboard_without_the_timezone_variable())

    assert [s for s in strings if DEFAULT_TIMEZONE in s] == []


def test_every_dashboard_day_conversion_takes_its_zone_from_the_variable() -> None:
    text = "\n".join(_strings(_dashboard_without_the_timezone_variable()))

    zones = _convert_tz_zones(text)

    assert zones, "no CONVERT_TZ calls found"
    assert [z for z in zones if z != {"UTC", "${timezone}"}] == []


def test_every_reference_query_conversion_takes_its_zone_from_the_variable() -> None:
    text = REFERENCE_QUERIES.read_text(encoding="utf-8")

    zones = _convert_tz_zones(text)

    assert zones, "no CONVERT_TZ calls found"
    assert [z for z in zones if z != {"UTC", "${timezone}"}] == []
    assert f"'{DEFAULT_TIMEZONE}'" not in text
