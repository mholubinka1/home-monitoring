import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from common.config import MariaDBSettings
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.mysql.model import SQLBase


@pytest.fixture
def mariadb_client(monkeypatch: pytest.MonkeyPatch) -> MariaDBClient:
    """A hive_app MariaDBClient backed by an in-memory SQLite database."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLBase.metadata.create_all(engine)

    monkeypatch.setattr(
        "common.mariadb.client.create_engine",
        lambda *args, **kwargs: engine,
    )

    settings = MariaDBSettings(
        host="localhost",
        port=3306,
        database="octopus",
        username="test",
        password="test",
    )
    return MariaDBClient(settings)
