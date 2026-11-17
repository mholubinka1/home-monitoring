from collections.abc import Callable
from datetime import UTC, datetime

from hive_app.data.mysql.client import MariaDBClient


def test_startup_creates_the_presence_period_table_when_it_is_missing(
    unsynced_mariadb_client_factory: Callable[[], MariaDBClient],
) -> None:
    client = unsynced_mariadb_client_factory()

    client.change_presence_state("someone_home", 1, datetime(2026, 11, 17, tzinfo=UTC))

    assert client.read_open_presence_state() == "someone_home"
