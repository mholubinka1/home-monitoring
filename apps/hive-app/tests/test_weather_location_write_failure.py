import pytest
from sqlalchemy import text

from common.exceptions import MariaDBError
from hive_app.data.model import ResolvedLocation
from hive_app.data.mysql.client import MariaDBClient

POSTCODE = "ZZ99 9ZZ"


def test_a_failed_weather_location_write_keeps_the_postcode_out_of_the_error_and_logs(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    # With the table gone the database rejects the INSERT, and SQLAlchemy's
    # error message would otherwise embed the bound postcode.
    with mariadb_client.session_write_scope() as session:
        session.execute(text("DROP TABLE weather_location"))

    with caplog.at_level("DEBUG"), pytest.raises(MariaDBError) as raised:
        mariadb_client.write_weather_location(
            ResolvedLocation(51.4, -0.05, "postcode", POSTCODE)
        )

    assert "Failed to write Weather location" in caplog.text
    assert POSTCODE not in str(raised.value)
    assert raised.value.__cause__ is None
    assert POSTCODE not in caplog.text
    assert all(POSTCODE not in str(record.exc_info) for record in caplog.records)
