import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from common.config import MariaDBSettings
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.mysql.model import SQLBase


@pytest.fixture
def mariadb_client(monkeypatch: pytest.MonkeyPatch) -> MariaDBClient:
    """A hive_app MariaDBClient backed by an in-memory SQLite database.

    schema="octopus" is translated to "main" (SQLite's default database) --
    see ADR-0025 for why this fixture's own map and database="main" below
    must agree with SessionBuilder's own schema_translate_map.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map={"octopus": "main"})
    SQLBase.metadata.create_all(engine)

    monkeypatch.setattr(
        "common.mariadb.client.create_engine",
        lambda *args, **kwargs: engine,
    )

    settings = MariaDBSettings(
        host="localhost",
        port=3306,
        database="main",
        username="test",
        password="test",
    )
    return MariaDBClient(settings)
