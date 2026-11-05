from common.mariadb.client import MariaDBClientBase

from .conftest import MariaDBContainer

POSTCODE = "SW1A 1AA"


def test_the_next_operation_succeeds_after_the_server_drops_every_client_connection(
    configured_client: MariaDBClientBase,
    mariadb_container: MariaDBContainer,
) -> None:
    # A completed write leaves a pooled connection that a server restart then kills.
    configured_client.write_account_postcode(POSTCODE)
    dropped = mariadb_container.kill_other_connections()
    assert dropped >= 1, "the client held no pooled connection for the server to drop"

    assert configured_client.read_account_postcode() == POSTCODE
