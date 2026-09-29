from typing import ClassVar

from sqlalchemy import Column, DateTime, Index, Integer, String
from sqlalchemy.ext.declarative import declarative_base

# The one table genuinely shared between octopus-app and hive-app. Each app's
# own declarative base must extend this SQLBase (not define a fresh one) so
# that job_run's table is created alongside that app's own tables in the same
# Schema Sync pass -- see ADR-0020.
SQLBase = declarative_base()


class job_run(SQLBase):
    __tablename__ = "job_run"
    __table_args__: ClassVar[tuple[Index]] = (
        Index("ix_job_run_job_name_ran_at", "job_name", "ran_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_name = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False)
    ran_at = Column(DateTime, nullable=False)
    error_message = Column(String(1000))
