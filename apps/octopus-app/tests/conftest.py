import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from common.config import MariaDBSettings
from octopus_app.data.mysql.client import MariaDBClient, weather_metadata
from octopus_app.data.mysql.model import SQLBase


@pytest.fixture
def mariadb_client(monkeypatch: pytest.MonkeyPatch) -> MariaDBClient:
    """A MariaDBClient backed by an in-memory SQLite database.

    Tables are declared with schema="octopus" for real MariaDB, which SQLite
    has no equivalent for, so the schema is translated away for this engine.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map={"octopus": None})
    SQLBase.metadata.create_all(engine)
    # weather_observation/weather_forecast are hive-app-owned tables read
    # cross-app by octopus-app's gas cost regression (see #511) -- declared
    # on their own unregistered MetaData in mysql/client.py rather than
    # SQLBase, so they need creating here explicitly too.
    weather_metadata.create_all(engine)

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
