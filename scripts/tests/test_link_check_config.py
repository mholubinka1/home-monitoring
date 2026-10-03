"""The markdown-link-check settings that keep the link check steady against connection-level
failures (`Status: 0`) without ceasing to check the links.

Incident: the checker intermittently reported `Status: 0` for
https://docs.octopus.energy/graphql/reference/mutations/ (200 when requested directly) and
failed Code Quality on `main` for the #567 merge. The failure could not be reproduced
locally (0 of 8 runs), so the fix is conservative hardening that is unproven locally: a
longer timeout and a browser-like User-Agent scoped to docs.octopus.energy. No ignore
entry: the link stays checked."""

import json
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).resolve().parents[2] / ".markdown-link-check.json"
DOCS_HOST = "https://docs.octopus.energy"


def _config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def test_the_timeout_is_long_enough_for_a_slow_connection() -> None:
    timeout = _config()["timeout"]

    assert int(timeout.removesuffix("s")) >= 20, f"timeout {timeout!r} is too short"


def test_docs_octopus_energy_is_requested_with_a_browser_like_user_agent() -> None:
    entries = [e for e in _config().get("httpHeaders", []) if DOCS_HOST in e["urls"]]

    assert entries, f"no httpHeaders entry scoped to {DOCS_HOST}"
    user_agent = entries[0]["headers"].get("User-Agent", "")
    assert user_agent.startswith("Mozilla/5.0"), f"not browser-like: {user_agent!r}"


def test_the_flaky_link_is_still_checked_not_ignored() -> None:
    ignored = [p["pattern"] for p in _config().get("ignorePatterns", [])]

    assert not any("docs.octopus.energy" in pattern for pattern in ignored)
