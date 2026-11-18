"""HiveApiSource.write_comfort_schedule: the whole week posted to Hive's heating
node in this call's own session, then read back (spec: feature-presence-
autonomous-heating, issue #808).

Hive's API is faked at the boundary: its `request` records the POST and its
`getDevices` stands in for the fresh product read, storing whatever each read is
scripted to show. Made-up slot: 05:40 to 06:25 at 19 C."""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hive_app.common.config import HiveSettings
from hive_app.data.hive_client import HiveApiSource
from hive_app.data.mysql.client import MariaDBClient

from .conftest import THERMOSTAT_HIVE_ID

_LOGGED_IN = {
    "AuthenticationResult": {
        "IdToken": "id-tok",
        "AccessToken": "access-tok",
        "RefreshToken": "refresh-tok",
    }
}
_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_NODES_URL = "https://hive.example/nodes/{0}/{1}"


def _week() -> dict[str, Any]:
    return {
        day: [
            {"start": 0, "value": {"target": 7.0}},
            {"start": 340, "value": {"target": 19.0}},
            {"start": 385, "value": {"target": 7.0}},
        ]
        for day in _DAYS
    }


class _Hive:
    """Wires a fake Hive session: the POSTs it received and the scripted reads."""

    def __init__(self, reads: list[dict[str, Any]]) -> None:
        self.posts: list[tuple[str, str, dict[str, Any]]] = []
        self.read_count = 0
        self._reads = reads

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.posts.append((method, url, kwargs))
        return SimpleNamespace(status=200)

    def stored_week_for_read(self) -> dict[str, Any]:
        index = min(self.read_count, len(self._reads) - 1)
        self.read_count += 1
        return copy.deepcopy(self._reads[index])


def _source(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reads: list[dict[str, Any]],
) -> tuple[HiveApiSource, _Hive]:
    fake = install_fake_hive(login_result=_LOGGED_IN, product_state={})
    hive = _Hive(reads)
    fake.api = SimpleNamespace(
        getAll=fake.api.getAll, request=hive.request, urls={"nodes": _NODES_URL}
    )

    async def get_devices(hive_id: str) -> bool:
        fake.session.data.products[hive_id]["state"][
            "schedule"
        ] = hive.stored_week_for_read()
        return True

    fake.session.getDevices = get_devices
    # The re-read waits a few seconds in production; not in a test.
    monkeypatch.setattr("hive_app.data.hive_client.SCHEDULE_REREAD_SECONDS", 0)
    settings = HiveSettings(
        username="user@example.com",
        password="hunter2",
        auth_state_path=str(tmp_path / "auth.json"),
    )
    return HiveApiSource(settings, mariadb_client), hive


def test_the_whole_week_is_posted_to_the_heating_node_and_read_back(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given Hive stores exactly what it is sent
    source, hive = _source(
        install_fake_hive, mariadb_client, tmp_path, monkeypatch, [_week()]
    )

    # When the week is written
    outcome = source.write_comfort_schedule(_week())

    # Then one POST goes to the heating node with the week as the JSON body, and
    # the read-back matched
    assert len(hive.posts) == 1
    method, url, kwargs = hive.posts[0]
    assert (method, url) == (
        "post",
        f"https://hive.example/nodes/heating/{THERMOSTAT_HIVE_ID}",
    )
    assert json.loads(kwargs["data"]) == {"schedule": _week()}
    assert outcome == "applied"


def test_a_read_back_that_differs_at_first_but_matches_on_a_second_read_is_applied(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given Hive's first read still shows the old week (the spike saw a whole-week
    # write read back different at 2 seconds), and the second shows the new one
    old = {day: [{"start": 0, "value": {"target": 7.0}}] for day in _DAYS}
    source, hive = _source(
        install_fake_hive, mariadb_client, tmp_path, monkeypatch, [old, _week()]
    )

    # When the week is written
    outcome = source.write_comfort_schedule(_week())

    # Then it is read twice and counts as applied
    assert hive.read_count == 2
    assert outcome == "applied"


def test_a_week_that_never_reads_back_as_sent_is_not_verified(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given Hive answers 200 but keeps showing the old week
    old = {day: [{"start": 0, "value": {"target": 7.0}}] for day in _DAYS}
    source, hive = _source(
        install_fake_hive, mariadb_client, tmp_path, monkeypatch, [old]
    )

    # When the week is written
    outcome = source.write_comfort_schedule(_week())

    # Then it was read once more after the first mismatch, and is not verified
    assert hive.read_count == 2
    assert outcome == "not_verified"


def _first_entry_not_at_midnight(week: dict[str, Any]) -> None:
    week["monday"][0]["start"] = 60


def _target_off_a_half_degree(week: dict[str, Any]) -> None:
    week["monday"][1]["value"]["target"] = 19.3


def _a_day_missing(week: dict[str, Any]) -> None:
    del week["sunday"]


def _an_empty_day(week: dict[str, Any]) -> None:
    week["monday"] = []


def _a_duplicate_start(week: dict[str, Any]) -> None:
    week["monday"][2]["start"] = 340


def _a_start_out_of_range(week: dict[str, Any]) -> None:
    week["monday"][2]["start"] = 1440


def _a_start_that_is_not_a_whole_number(week: dict[str, Any]) -> None:
    week["monday"][2]["start"] = 385.5


@pytest.mark.parametrize(
    "break_it",
    [
        _first_entry_not_at_midnight,
        _target_off_a_half_degree,
        _a_day_missing,
        _an_empty_day,
        _a_duplicate_start,
        _a_start_out_of_range,
        _a_start_that_is_not_a_whole_number,
    ],
)
def test_a_week_that_breaks_a_rule_is_refused_before_anything_is_sent(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    break_it: Callable[[dict[str, Any]], None],
) -> None:
    # Given a week that breaks one of the write rules
    source, hive = _source(
        install_fake_hive, mariadb_client, tmp_path, monkeypatch, [_week()]
    )
    week = _week()
    break_it(week)

    # When the week is written
    # Then it is refused, and Hive was sent nothing
    with pytest.raises(ValueError):
        source.write_comfort_schedule(week)
    assert not hive.posts
