import pytest

from common.exceptions import MariaDBError
from common.mariadb.client import MariaDBClientBase
from common.mariadb.model import account_postcode

from .conftest import CONFIGURED_DATABASE, MariaDBContainer

POSTCODE = "SW1A 1AA"


def test_schema_sync_creates_account_postcode_in_an_empty_database_with_no_postcode_yet(
    configured_client: MariaDBClientBase,
    mariadb_container: MariaDBContainer,
) -> None:
    assert "account_postcode" in mariadb_container.table_names(CONFIGURED_DATABASE)
    assert configured_client.read_account_postcode() is None


def test_a_written_account_postcode_is_read_back(
    configured_client: MariaDBClientBase,
) -> None:
    configured_client.write_account_postcode(POSTCODE)

    assert configured_client.read_account_postcode() == POSTCODE


def test_writing_a_different_account_postcode_overwrites_it_in_the_single_row(
    configured_client: MariaDBClientBase,
) -> None:
    configured_client.write_account_postcode(POSTCODE)
    configured_client.write_account_postcode("EC1A 1BB")

    assert configured_client.read_account_postcode() == "EC1A 1BB"
    with configured_client.session_read_scope() as session:
        rows = session.query(account_postcode).all()
        assert [row.id for row in rows] == [1]


def test_a_failed_account_postcode_write_keeps_the_postcode_out_of_the_error_and_logs(
    configured_client: MariaDBClientBase,
    mariadb_container: MariaDBContainer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Dropping the table makes the real server reject the INSERT, whose
    # SQLAlchemy error message would otherwise embed the bound postcode.
    mariadb_container.run_sql("DROP TABLE account_postcode;", CONFIGURED_DATABASE)

    with caplog.at_level("DEBUG"), pytest.raises(MariaDBError) as raised:
        configured_client.write_account_postcode(POSTCODE)

    assert POSTCODE not in str(raised.value)
    assert POSTCODE not in caplog.text
