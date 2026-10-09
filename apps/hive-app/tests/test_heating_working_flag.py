import logging
from collections.abc import Callable
from datetime import UTC, datetime
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


def _source(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
    reported: object,
) -> HiveApiSource:
    install_fake_hive(login_result=_LOGGED_IN, heating_working=reported)
    settings = HiveSettings(
        username="user@example.com",
        password="hunter2",
        auth_state_path=str(tmp_path / "auth.json"),
    )
    return HiveApiSource(settings, mariadb_client)


def _status(working: bool | None) -> HeatingStatus:
    return HeatingStatus(
        polled_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
        current_temp=19.5,
        target_temp=21.0,
        mode="SCHEDULE",
        state="ON",
        boost_active=False,
        boost_ends_at=None,
        schedule={"now": "21.0"},
        working=working,
    )


@pytest.mark.parametrize("working", [True, False, None])
def test_the_thermostats_own_working_report_is_stored_with_the_poll(
    mariadb_client: MariaDBClient, working: bool | None
) -> None:
    mariadb_client.write_heating_status(_status(working))

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.heating_status).one()

    assert stored.working is working


@pytest.mark.parametrize("reported", [True, False])
def test_a_poll_carries_the_thermostats_working_report(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
    reported: bool,
) -> None:
    source = _source(install_fake_hive, mariadb_client, tmp_path, reported)

    assert source.fetch_heating_status().working is reported


@pytest.mark.parametrize("reported", [None, "ON", 1])
def test_a_missing_or_unexpected_working_report_is_stored_as_null_and_logged_once(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    reported: object,
) -> None:
    source = _source(install_fake_hive, mariadb_client, tmp_path, reported)

    with caplog.at_level(logging.WARNING):
        status = source.fetch_heating_status()

    assert status.working is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "working" in warnings[0].getMessage()
    assert type(reported).__name__ in warnings[0].getMessage()
