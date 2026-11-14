"""Seed helpers shared by the Room Forecast job's test modules.

London is on GMT in November, so local time is UTC."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from octopus_app.data.model import ComfortDayRecord, ThermalModelRecord
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import (
    MariaDBClient,
    heating_schedule_table,
    heating_status_table,
    weather_forecast_hour_table,
)

# A Tuesday.
NOW = datetime(2026, 11, 17, 14, 10, tzinfo=UTC)
ONE_HOUR = timedelta(hours=1)
LOCATION = "51.50,-0.10"
REGION = "A"
DAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def usable_model() -> ThermalModelRecord:
    return ThermalModelRecord(
        fitted_at=NOW - timedelta(hours=5),
        window_start=NOW.date() - timedelta(days=60),
        window_end=NOW.date() - timedelta(days=1),
        usable=True,
        unusable_reason=None,
        steps_used=1000,
        heating_steps=200,
        loss_per_degree=0.02,
        heating_gain=1.3,
        carried_gain=0.3,
        sun_gain=0.4,
        constant_gain=0.08,
        backtest_days=10,
        backtest_mean_error=0.2,
        backtest_worst_error=0.6,
    )


def weekly_schedule(target_on_day: dict[str, float] | None = None) -> dict[str, Any]:
    """Hive's weekly schedule: one slot from midnight a day, at 7 C unless the
    day has its own target."""
    targets = target_on_day or {}
    return {
        day: [{"start": 0, "value": {"target": targets.get(day, 7.0)}}] for day in DAYS
    }


def seed_reading(
    client: MariaDBClient, at: datetime, room: float, mode: str = "SCHEDULE"
) -> None:
    with client.session_write_scope() as session:
        session.execute(
            heating_status_table.insert(),
            [
                {
                    "polled_at": at.replace(tzinfo=None),
                    "current_temp": room,
                    "target_temp": 7.0,
                    "mode": mode,
                    "state": "OFF",
                    "boost_active": False,
                }
            ],
        )


def seed_schedule(client: MariaDBClient, schedule: dict[str, Any]) -> None:
    with client.session_write_scope() as session:
        session.execute(
            heating_schedule_table.insert(),
            [{"captured_at": NOW.replace(tzinfo=None), "schedule": schedule}],
        )


def seed_weather_forecast(
    client: MariaDBClient, first: datetime, last: datetime, outdoor: float = 10.0
) -> None:
    """The hourly forecast for every stamp from `first` to `last` (naive UTC):
    steady outdoor temperature, no sunshine."""
    stamps = []
    stamp = first
    while stamp <= last:
        stamps.append(
            {
                "source": "open-meteo",
                "location": LOCATION,
                "forecast_at": stamp,
                "temp": outdoor,
                "shortwave_radiation": 0.0,
                "fetched_at": NOW.replace(tzinfo=None),
            }
        )
        stamp += ONE_HOUR
    with client.session_write_scope() as session:
        session.execute(weather_forecast_hour_table.insert(), stamps)


def seed_weather_stamps(
    client: MariaDBClient, stamps: list[tuple[datetime, float, float | None]]
) -> None:
    """(stamp, outdoor temperature, shortwave radiation) rows of the forecast."""
    with client.session_write_scope() as session:
        session.execute(
            weather_forecast_hour_table.insert(),
            [
                {
                    "source": "open-meteo",
                    "location": LOCATION,
                    "forecast_at": stamp,
                    "temp": temp,
                    "shortwave_radiation": radiation,
                    "fetched_at": NOW.replace(tzinfo=None),
                }
                for stamp, temp, radiation in stamps
            ],
        )


def live_rows(client: MariaDBClient, scenario: str) -> list[Any]:
    rows = model.room_forecast_hour
    with client.session_read_scope() as session:
        return (
            session.query(rows)
            .filter(rows.kind == "live", rows.scenario == scenario)
            .order_by(rows.hour_start)
            .all()
        )


def seed_gas_rate(client: MariaDBClient, unit_rate_p: float) -> None:
    valid_from = datetime(2026, 4, 1)
    with client.session_write_scope() as session:
        session.add(
            model.agreement(
                id="G-1",
                energy="G",
                product_code="VAR-22-11-01",
                tariff_code="G-1R-VAR-22-11-01-A",
                valid_from=valid_from,
                valid_to=None,
            )
        )
        session.add(
            model.product_rate(
                id="G-rate-1",
                product_code="VAR-22-11-01",
                region=REGION,
                valid_from=valid_from,
                valid_to=None,
                unit_rate=unit_rate_p,
                standing_charge=29.0,
            )
        )


def seed_measured_hours(client: MariaDBClient, count: int, kw: float) -> None:
    """`count` measured hours in the last month, each on for 30 minutes at `kw`."""
    first = datetime(2026, 11, 10, 10)
    with client.session_write_scope() as session:
        session.add_all(
            model.heating_hour(
                hour_start=first + ONE_HOUR * index,
                poll_count=30,
                on_minutes=30,
                measured_kwh=kw / 2 + 0.3,
                heating_kwh=kw / 2,
                source="measured",
                unit_rate=6.0,
                cost_gbp=0.1,
                computed_at=NOW.replace(tzinfo=None),
            )
            for index in range(count)
        )


def seed_heating_forecast_inputs(client: MariaDBClient) -> None:
    """Tuesday's target is 19 C and the room 18 C, so the first hour heats 50
    minutes."""
    seed_reading(client, NOW - timedelta(minutes=5), room=18.0)
    seed_schedule(client, weekly_schedule({"tuesday": 19.0}))
    seed_weather_forecast(
        client,
        first=datetime(2026, 11, 17, 14),
        last=datetime(2026, 11, 19, 0),
    )


def seed_comfort(client: MariaDBClient, day: date, comfort_temp: float | None) -> None:
    client.write_comfort_days(
        [
            ComfortDayRecord(
                date=day,
                comfort_temp=comfort_temp,
                boost_starts=0,
                counted_hours="",
                minutes_below=None,
                computed_at=NOW.replace(tzinfo=None),
            )
        ]
    )


def seed_plan_inputs(client: MariaDBClient, room: float = 18.0) -> None:
    """A fresh reading, a 7 C schedule and weather to the end of tomorrow."""
    seed_reading(client, NOW - timedelta(minutes=5), room=room)
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 14), last=datetime(2026, 11, 19, 0)
    )


@pytest.fixture(name="client")
def thermal_model_client(mariadb_client: MariaDBClient) -> MariaDBClient:
    mariadb_client.write_thermal_model(usable_model())
    return mariadb_client
