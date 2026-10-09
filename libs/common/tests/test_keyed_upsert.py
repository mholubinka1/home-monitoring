import logging

from sqlalchemy import Column, Index, Integer, String
from sqlalchemy.ext.declarative import declarative_base

from common.mariadb.client import MariaDBClientBase

from .conftest import CONFIGURED_DATABASE, MariaDBContainer

# A throwaway model with a unique key other than its primary key, standing in
# for any app table that is written through the keyed upsert.
_KeyedBase = declarative_base()


class _Reading(_KeyedBase):  # type: ignore[misc,valid-type]
    __tablename__ = "keyed_reading"
    __table_args__ = (
        Index("uq_keyed_reading", "source", "hour", unique=True),
        {"schema": "octopus"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(20))
    hour = Column(String(20), nullable=False)
    value = Column(Integer)


def test_writing_a_keyed_record_twice_on_a_real_database_leaves_one_row_with_the_latest_value(
    mariadb_container: MariaDBContainer,
) -> None:
    mariadb_container.run_sql(f"CREATE DATABASE {CONFIGURED_DATABASE};")
    client = MariaDBClientBase(
        mariadb_container.settings_for(CONFIGURED_DATABASE),
        declarative_base=_KeyedBase,
        logger=logging.getLogger("test"),
    )
    key = ("source", "hour")

    client._write_all(  # pylint: disable=protected-access
        [_Reading(source="a", hour="12:00", value=1)], "reading", key_columns=key
    )
    client._write_all(  # pylint: disable=protected-access
        [_Reading(source="a", hour="12:00", value=2)], "reading", key_columns=key
    )
    client._write_all(  # pylint: disable=protected-access
        [_Reading(source="b", hour="12:00", value=3)], "reading", key_columns=key
    )

    with client.session_read_scope() as session:
        rows = {(r.source, r.value) for r in session.query(_Reading).all()}
    assert rows == {("a", 2), ("b", 3)}
    assert "uq_keyed_reading" in mariadb_container.index_names(
        "keyed_reading", CONFIGURED_DATABASE
    )
