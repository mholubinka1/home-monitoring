"""HiveApiSource.write_comfort_schedule: the whole week posted once to Hive's
heating node in this call's own session, with no read-back, and not posted while
that session finds the product offline (spec: feature-presence-autonomous-heating,
issue #808; bugfix-comfort-schedule-write-only-while-hive-online, issue #826).

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
from apyhiveapi.helper.hive_exceptions import HiveApiError

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
        # What the library's request raises for a non-2xx answer, when set.
        self.refusal: Exception | None = None

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.posts.append((method, url, kwargs))
        if self.refusal is not None:
            raise self.refusal
        return SimpleNamespace(status=200)

    def stored_week_for_read(self) -> dict[str, Any]:
        index = min(self.read_count, len(self._reads) - 1)
        self.read_count += 1
        return copy.deepcopy(self._reads[index])


def _source(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
    reads: list[dict[str, Any]],
    product_props: dict[str, Any] | None = None,
) -> tuple[HiveApiSource, _Hive]:
    fake = install_fake_hive(
        login_result=_LOGGED_IN, product_state={}, product_props=product_props
    )
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
    settings = HiveSettings(
        username="user@example.com",
        password="hunter2",
        auth_state_path=str(tmp_path / "auth.json"),
    )
    return HiveApiSource(settings, mariadb_client), hive


def test_the_whole_week_is_posted_once_with_no_read_back_and_is_sent(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
) -> None:
    # Given Hive keeps showing the old week (it echoes writes it never delivers,
    # so a read in the same session proves nothing)
    old = {day: [{"start": 390, "value": {"target": 7.0}}] for day in _DAYS}
    source, hive = _source(install_fake_hive, mariadb_client, tmp_path, [old])

    # When the week is written
    outcome = source.write_comfort_schedule(_week())

    # Then one POST goes to the heating node with the week as the JSON body, Hive
    # is not read back, and the week is sent
    assert len(hive.posts) == 1
    method, url, kwargs = hive.posts[0]
    assert (method, url) == (
        "post",
        f"https://hive.example/nodes/heating/{THERMOSTAT_HIVE_ID}",
    )
    assert json.loads(kwargs["data"]) == {"schedule": _week()}
    assert hive.read_count == 0
    assert outcome == "sent"


def test_a_week_whose_days_have_no_midnight_entry_is_sent(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
) -> None:
    # Given a week shaped like Hive's own, with no entry at 00:00 on any day (the
    # night carries on at the previous day's last setting)
    week = {
        day: [
            {"start": 340, "value": {"target": 19.0}},
            {"start": 385, "value": {"target": 10.0}},
        ]
        for day in _DAYS
    }
    source, hive = _source(install_fake_hive, mariadb_client, tmp_path, [week])

    # When the week is written
    outcome = source.write_comfort_schedule(week)

    # Then it is posted as it is and sent
    assert len(hive.posts) == 1
    _, _, kwargs = hive.posts[0]
    assert json.loads(kwargs["data"]) == {"schedule": week}
    assert outcome == "sent"


def test_a_write_hive_refuses_while_online_still_raises(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
) -> None:
    # Given Hive is online but answers the POST with a non-2xx status (the
    # library's request then raises HiveApiError)
    source, hive = _source(install_fake_hive, mariadb_client, tmp_path, [_week()])
    hive.refusal = HiveApiError()

    # When the week is written
    # Then the refusal is raised, for the control to store as failed
    with pytest.raises(HiveApiError):
        source.write_comfort_schedule(_week())


def test_a_week_is_not_posted_when_the_writes_own_session_finds_hive_offline(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
) -> None:
    # Given the write's own session finds the heating product offline (Hive
    # answers such a write with 404 NOT_FOUND, or echoes it and never delivers it)
    source, hive = _source(
        install_fake_hive,
        mariadb_client,
        tmp_path,
        [_week()],
        product_props={"online": False},
    )

    # When the week is written
    outcome = source.write_comfort_schedule(_week())

    # Then nothing is posted and the outcome is offline
    assert not hive.posts
    assert outcome == "offline"


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


def _an_entry_without_a_value(week: dict[str, Any]) -> None:
    del week["monday"][1]["value"]


def _a_day_that_is_not_a_list(week: dict[str, Any]) -> None:
    week["monday"] = "not a list of entries"


@pytest.mark.parametrize(
    "break_it",
    [
        _an_entry_without_a_value,
        _a_day_that_is_not_a_list,
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
    break_it: Callable[[dict[str, Any]], None],
) -> None:
    # Given a week that breaks one of the write rules
    source, hive = _source(install_fake_hive, mariadb_client, tmp_path, [_week()])
    week = _week()
    break_it(week)

    # When the week is written
    # Then it is refused, and Hive was sent nothing
    with pytest.raises(ValueError):
        source.write_comfort_schedule(week)
    assert not hive.posts
