from datetime import UTC, datetime
from typing import ClassVar

import pytest
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.pool import StaticPool

from common.config import MariaDBSettings
from hive_app.data.mysql.client import MariaDBClient

# heating_status as it was before the thermostat's working report was kept.
_LegacyBase = declarative_base()


class _LegacyHeatingStatus(_LegacyBase):  # type: ignore[misc,valid-type]
    __tablename__ = "heating_status"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    polled_at = Column(DateTime, nullable=False)
    current_temp = Column(Float)
    target_temp = Column(Float)
    mode = Column(String(20))
    state = Column(String(20))
    boost_active = Column(Boolean)
    boost_ends_at = Column(DateTime)
    schedule = Column(JSON)


def test_existing_heating_status_gains_the_working_column_and_keeps_its_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map={"octopus": "main"})
    _LegacyBase.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO heating_status (polled_at, current_temp, state) "
                "VALUES (:polled_at, 19.5, 'ON')"
            ),
            {"polled_at": datetime(2026, 9, 1, 12, 0, tzinfo=UTC)},
        )
    monkeypatch.setattr(
        "common.mariadb.client.create_engine", lambda *args, **kwargs: engine
    )

    MariaDBClient(
        MariaDBSettings(
            host="localhost",
            port=3306,
            database="main",
            username="test",
            password="test",
        )
    )

    columns = {c["name"] for c in inspect(engine).get_columns("heating_status")}
    assert "working" in columns
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT state, working FROM heating_status")
        ).one()
    assert tuple(row) == ("ON", None)
