import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from common.config import MariaDBSettings
from common.mariadb.client import MariaDBClientBase
from common.mariadb.model import SQLBase


@pytest.fixture
def mariadb_client(monkeypatch: pytest.MonkeyPatch) -> MariaDBClientBase:
    """A MariaDBClientBase backed by an in-memory SQLite database."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

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
    return MariaDBClientBase(
        settings, declarative_base=SQLBase, logger=logging.getLogger("test")
    )
