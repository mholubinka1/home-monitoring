from collections.abc import Callable

from hive_app.data.mysql.client import MariaDBClient


def test_hive_app_starting_first_still_provides_an_empty_account_postcode(
    unsynced_mariadb_client_factory: Callable[[], MariaDBClient],
) -> None:
    # The database starts empty: the table can only exist if hive_app's own
    # startup Schema Sync created it.
    client = unsynced_mariadb_client_factory()

    assert client.read_account_postcode() is None

    client.write_account_postcode("SW1A 1AA")

    assert client.read_account_postcode() == "SW1A 1AA"
