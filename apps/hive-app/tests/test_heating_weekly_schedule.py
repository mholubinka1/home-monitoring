"""The Weekly Schedule: Hive's full seven-day heating schedule, stored one row
each time it changes (ADR-0035).

The shape below is the one apyhiveapi's HiveHelper.getScheduleNNL reads from a
product's `state.schedule`: a list of slots per day name, each with `start`
(minutes after local midnight) and `value.target`."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hive_app.common.config import HiveSettings
from hive_app.data.hive_client import HiveApiSource
from hive_app.data.model import HeatingStatus
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient

_LOGGED_IN = {
    "AuthenticationResult": {
        "IdToken": "id-tok",
        "AccessToken": "access-tok",
        "RefreshToken": "refresh-tok",
    }
}
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")
_WEEKEND = ("saturday", "sunday")


def _weekly_schedule(weekday_morning_target: float = 18) -> dict[str, Any]:
    def day(morning_start: int) -> list[dict[str, Any]]:
        return [
            {"start": morning_start, "value": {"target": weekday_morning_target}},
            {"start": morning_start + 45, "value": {"target": 7}},
        ]

    return {name: day(390) for name in _WEEKDAYS} | {
        name: day(465) for name in _WEEKEND
    }


def _source(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
    product_state: dict[str, Any],
) -> HiveApiSource:
    install_fake_hive(login_result=_LOGGED_IN, product_state=product_state)
    settings = HiveSettings(
        username="user@example.com",
        password="hunter2",
        auth_state_path=str(tmp_path / "auth.json"),
    )
    return HiveApiSource(settings, mariadb_client)


def test_a_poll_carries_hives_weekly_schedule(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
) -> None:
    source = _source(
        install_fake_hive, mariadb_client, tmp_path, {"schedule": _weekly_schedule()}
    )

    assert source.fetch_heating_status().weekly_schedule == _weekly_schedule()


def _poll_and_persist(source: HiveApiSource) -> HeatingStatus:
    status = source.fetch_heating_status()
    source.persist_heating_status(status)
    return status


def _stored_schedules(mariadb_client: MariaDBClient) -> list[Any]:
    with mariadb_client.session_read_scope() as session:
        return (
            session.query(model.heating_schedule)
            .order_by(model.heating_schedule.id)
            .all()
        )


def test_the_first_weekly_schedule_is_stored_with_the_time_of_its_poll(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
) -> None:
    source = _source(
        install_fake_hive, mariadb_client, tmp_path, {"schedule": _weekly_schedule()}
    )

    status = _poll_and_persist(source)

    stored = _stored_schedules(mariadb_client)
    assert [(row.captured_at, row.schedule) for row in stored] == [
        (status.polled_at.replace(tzinfo=None), _weekly_schedule())
    ]


def test_an_unchanged_weekly_schedule_adds_no_row(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
) -> None:
    source = _source(
        install_fake_hive, mariadb_client, tmp_path, {"schedule": _weekly_schedule()}
    )

    _poll_and_persist(source)
    _poll_and_persist(source)

    assert len(_stored_schedules(mariadb_client)) == 1


def test_a_changed_weekly_schedule_adds_a_row_and_keeps_the_earlier_one(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
) -> None:
    _poll_and_persist(
        _source(
            install_fake_hive,
            mariadb_client,
            tmp_path,
            {"schedule": _weekly_schedule(weekday_morning_target=18)},
        )
    )
    _poll_and_persist(
        _source(
            install_fake_hive,
            mariadb_client,
            tmp_path,
            {"schedule": _weekly_schedule(weekday_morning_target=20)},
        )
    )

    stored = _stored_schedules(mariadb_client)
    assert [row.schedule for row in stored] == [
        _weekly_schedule(weekday_morning_target=18),
        _weekly_schedule(weekday_morning_target=20),
    ]


@pytest.mark.parametrize(
    "product_state",
    [{}, {"schedule": ["not", "a", "dict"]}],
    ids=["missing", "not-a-dictionary"],
)
def test_a_poll_without_a_usable_weekly_schedule_is_stored_without_a_schedule_row(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
    product_state: dict[str, Any],
) -> None:
    source = _source(install_fake_hive, mariadb_client, tmp_path, product_state)

    _poll_and_persist(source)

    with mariadb_client.session_read_scope() as session:
        status_rows = session.query(model.heating_status).count()
    assert status_rows == 1
    assert not _stored_schedules(mariadb_client)


def test_a_database_created_before_the_weekly_schedule_gains_its_table_on_startup(
    unsynced_mariadb_client_factory: Callable[[], MariaDBClient],
) -> None:
    client = unsynced_mariadb_client_factory()

    assert not _stored_schedules(client)
