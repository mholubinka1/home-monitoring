from sqlalchemy import JSON, Boolean, Column, Date, DateTime, Float, Integer, String

from common.mariadb.model import SQLBase, job_run

__all__ = ["SQLBase", "job_run"]


class heating_status(SQLBase):
    __tablename__ = "heating_status"

    id = Column(Integer, primary_key=True, autoincrement=True)
    polled_at = Column(DateTime, nullable=False)
    current_temp = Column(Float)
    target_temp = Column(Float)
    mode = Column(String(20))
    state = Column(String(20))
    boost_active = Column(Boolean)
    boost_ends_at = Column(DateTime)
    schedule = Column(JSON)


class weather_observation(SQLBase):
    __tablename__ = "weather_observation"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(20))
    observed_at = Column(DateTime, nullable=False)
    temp = Column(Float)
    humidity = Column(Float)
    pressure = Column(Float)
    wind_speed = Column(Float)
    precipitation = Column(Float)


class weather_forecast(SQLBase):
    __tablename__ = "weather_forecast"

    id = Column(String(50), primary_key=True)
    source = Column(String(20))
    target_date = Column(Date, nullable=False)
    max_temp = Column(Float)
    fetched_at = Column(DateTime, nullable=False)
