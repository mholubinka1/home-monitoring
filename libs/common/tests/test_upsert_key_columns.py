import pytest
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from common.mariadb.client import upsert

_Base = declarative_base()


class _Reading(_Base):  # type: ignore[misc,valid-type]
    __tablename__ = "upsert_reading"

    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False)
    value = Column(Integer, nullable=False)


@pytest.fixture(name="session")
def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_an_empty_key_columns_is_rejected_rather_than_treated_as_the_primary_key(
    session: Session,
) -> None:
    with pytest.raises(ValueError, match="key_columns"):
        upsert(session, _Reading(id=1, source="a", value=1), key_columns=())


def test_a_conflict_matching_no_row_names_the_matched_columns_not_the_primary_key(
    session: Session,
) -> None:
    # The NOT NULL violation is not a key conflict, so no row matches on the
    # key columns and the failure must say so without blaming the primary key.
    record = _Reading(id=1, source="a", value=None)  # type: ignore[misc]

    with pytest.raises(RuntimeError, match=r"matched \{'source': 'a'\}") as caught:
        upsert(session, record, key_columns=("source",))

    assert "primary key" not in str(caught.value)
