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
    has no equivalent for, so the schema is translated to "main" (SQLite's
    own name for its default/only database) for this engine. The map is
    applied here (not left to SessionBuilder's own schema_translate_map --
    see common/mariadb/client.py and ADR-0025) because create_all below runs
    directly against this engine, before MariaDBClient/SessionBuilder ever
    sees it; database="main" below matches it so SessionBuilder's own map
    re-applies the same "octopus" -> "main" translation rather than a
    conflicting one.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map={"octopus": "main"})
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
        database="main",
        username="test",
        password="test",
    )
    return MariaDBClient(settings)


@pytest.fixture
def mariadb_client_without_weather_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> MariaDBClient:
    # Simulates hive-app never having run: only octopus-app's own tables
    # exist (SQLBase.metadata), not weather_observation/weather_forecast
    # (weather_metadata, created separately by hive-app's own Schema Sync in
    # production) -- proves the gas weather-regression read methods degrade
    # gracefully rather than raising when those tables genuinely don't
    # exist yet, matching the documented "hive-app hasn't run yet" scenario
    # (#511's acceptance criteria).
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
