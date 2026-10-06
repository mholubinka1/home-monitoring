from hive_app.data.mysql.client import MariaDBClient


def test_hive_app_starting_first_still_provides_an_empty_account_postcode(
    mariadb_client: MariaDBClient,
) -> None:
    assert mariadb_client.read_account_postcode() is None
