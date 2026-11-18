"""The Presence panel of the gas and heating dashboard: a state timeline of the last
seven days of Presence Periods."""

import json
import re
from pathlib import Path

GRAFANA_DIR = Path(__file__).resolve().parents[3] / "data" / "grafana"
DASHBOARD = GRAFANA_DIR / "gas-heating-dashboard.json"
PRESENCE = "Presence"
COST_COMPARISON = "Daily Cost: Schedules vs Actual"
GRID_COLUMNS = 24


def _panel(title: str) -> dict:
    panels = json.loads(DASHBOARD.read_text(encoding="utf-8"))["panels"]
    return next(p for p in panels if p["title"] == title)


def _presence_sql() -> str:
    return " ".join(t["rawSql"] for t in _panel(PRESENCE)["targets"])


def test_the_presence_panel_is_a_full_width_timeline_below_the_cost_comparison() -> (
    None
):
    panel = _panel(PRESENCE)
    comparison = _panel(COST_COMPARISON)

    assert panel["type"] == "state-timeline"
    assert panel["gridPos"]["w"] == GRID_COLUMNS
    assert panel["gridPos"]["x"] == 0
    assert panel["gridPos"]["y"] == (
        comparison["gridPos"]["y"] + comparison["gridPos"]["h"]
    )


def test_the_presence_panel_reads_each_period_as_a_utc_time_and_a_state() -> None:
    panel = _panel(PRESENCE)
    sql = _presence_sql()

    assert [t["format"] for t in panel["targets"]] == ["table"]
    # the dashboard's London timezone turns the UTC instants into London time
    assert "timezone" not in panel
    assert re.search(r"\bAS time\b", sql)
    assert re.search(r"\bAS state\b", sql)
    ctes = set(re.findall(r"\b(\w+) AS \(", sql))
    tables = set(re.findall(r"\b(?:FROM|JOIN) (\w+)", sql)) - ctes
    assert tables == {"presence_period"}


def test_the_presence_panel_shows_the_last_seven_days_and_clips_a_period_begun_earlier() -> (
    None
):
    sql = _presence_sql()

    # the panel's relative range makes the axis a week long whatever the
    # dashboard range; the query holds the same window itself, so it never
    # depends on $__timeFilter
    assert _panel(PRESENCE)["timeFrom"] == "7d"
    assert "$__timeFilter" not in sql
    assert "UTC_TIMESTAMP() - INTERVAL 7 DAY" in sql
    # a period still running at the window's start is drawn from the start,
    # and one that ended before it is left out
    assert "GREATEST(started_at, UTC_TIMESTAMP() - INTERVAL 7 DAY) AS time" in sql
    assert (
        "COALESCE(ended_at, UTC_TIMESTAMP()) > UTC_TIMESTAMP() - INTERVAL 7 DAY" in sql
    )


def test_the_presence_period_still_open_is_drawn_up_to_now() -> None:
    sql = _presence_sql()

    # a state timeline draws a band only from one row to the next, so the open
    # period gets a second row at now carrying its own state
    assert "UNION ALL" in sql
    open_branch = sql[sql.index("UNION ALL") :]
    assert "UTC_TIMESTAMP() AS time" in open_branch
    assert "state AS state" in open_branch
    assert "ended_at IS NULL" in open_branch


def test_each_presence_state_has_its_own_colour_and_a_plain_name() -> None:
    mappings = _panel(PRESENCE)["fieldConfig"]["defaults"]["mappings"]

    assert [m["type"] for m in mappings] == ["value"]
    options = mappings[0]["options"]
    assert {state: (o["text"], o["color"]) for state, o in options.items()} == {
        "someone_home": ("Someone home", "green"),
        "no_one_home": ("No one home", "blue"),
        "unknown": ("Unknown", "gray"),
    }
