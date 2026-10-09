from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal


@dataclass
class HeatingStatus:
    polled_at: datetime
    current_temp: float
    target_temp: float
    mode: str
    state: str
    boost_active: bool
    boost_ends_at: datetime | None
    schedule: dict[str, Any]


@dataclass
class WeatherObservation:
    source: str
    location: str
    observed_at: datetime
    temp: float
    humidity: float
    pressure: float
    wind_speed: float
    precipitation: float


@dataclass
class WeatherForecastDay:
    source: str
    target_date: date
    max_temp: float
    fetched_at: datetime


@dataclass
class ResolvedLocation:
    latitude: float
    longitude: float
    source: Literal["postcode", "ip"]


@dataclass
class HiveAuthState:
    refresh_token: str
    device_group_key: str
    device_key: str
    # apyhiveapi's device SRP flow (DEVICE_SRP_AUTH) requires this alongside
    # device_group_key/device_key -- omitting it (e.g. leaving it blank)
    # means a resume/refresh can never actually authenticate as the
    # remembered device, even with a valid refresh token.
    device_password: str
    updated_at: datetime
