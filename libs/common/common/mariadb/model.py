from typing import ClassVar

from sqlalchemy import Column, DateTime, Index, Integer, String
from sqlalchemy.ext.declarative import declarative_base

# The tables genuinely shared between octopus-app and hive-app (job_run, and
# account_postcode -- see ADR-0028). Each app's own declarative base must
# extend this SQLBase (not define a fresh one) so that these tables are created
# alongside that app's own tables in the same Schema Sync pass -- see ADR-0020.
SQLBase = declarative_base()


class job_run(SQLBase):
    __tablename__ = "job_run"
    __table_args__: ClassVar[tuple[Index, dict[str, str]]] = (
        Index("ix_job_run_job_name_ran_at", "job_name", "ran_at"),
        {"schema": "octopus"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_name = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False)
    ran_at = Column(DateTime, nullable=False)
    error_message = Column(String(1000))


class account_postcode(SQLBase):
    """One row (fixed id=1): the Octopus account's property postcode.
    octopus-app is the only writer; hive-app only reads it. Lives here
    beside job_run so both apps' Schema Sync create it regardless of start
    order -- see ADR-0028."""

    __tablename__ = "account_postcode"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    id = Column(Integer, primary_key=True)
    postcode = Column(String(20), nullable=False)
    updated_at = Column(DateTime, nullable=False)
