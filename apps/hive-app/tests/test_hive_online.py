"""Hive Online: each heating poll records whether Hive reports the heating product
reachable (spec: bugfix-comfort-schedule-write-only-while-hive-online).

Driven through HiveApiSource.fetch_heating_status and persist_heating_status with
Hive faked at the boundary and the real MariaDB test client underneath."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from hive_app.common.config import HiveSettings
from hive_app.data.hive_client import HiveApiSource
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
    product_props: dict[str, Any] | None,
) -> HiveApiSource:
    install_fake_hive(login_result=_LOGGED_IN, product_props=product_props)
    settings = HiveSettings(
        username="user@example.com",
        password="hunter2",
        auth_state_path=str(tmp_path / "auth.json"),
    )
    return HiveApiSource(settings, mariadb_client)


def _stored_online(mariadb_client: MariaDBClient) -> list[bool | None]:
    with mariadb_client.session_read_scope() as session:
        return [row.online for row in session.query(model.heating_status).all()]


def test_a_poll_of_an_offline_heating_product_is_stored_as_offline(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
) -> None:
    # Given Hive reports the heating product offline
    source = _source(install_fake_hive, mariadb_client, tmp_path, {"online": False})

    # When the heating poll runs
    source.persist_heating_status(source.fetch_heating_status())

    # Then the stored poll has online false
    assert _stored_online(mariadb_client) == [False]


def test_a_poll_with_no_online_flag_is_stored_as_unknown(
    install_fake_hive: Callable[..., Any],
    mariadb_client: MariaDBClient,
    tmp_path: Path,
) -> None:
    # Given Hive's product has no online flag
    source = _source(install_fake_hive, mariadb_client, tmp_path, None)

    # When the heating poll runs
    source.persist_heating_status(source.fetch_heating_status())

    # Then the stored poll has online NULL
    assert _stored_online(mariadb_client) == [None]
