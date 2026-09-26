from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from octopus_app.data.local_day import start_of_local_day
from octopus_app.data.mysql import model


def seed_daily_consumption_summary(
    s: Session, day: date, total_kwh: str, energy: str = "G"
) -> None:
    s.add(
        model.daily_consumption_summary(
            energy=energy, date=day, total_kwh=Decimal(total_kwh)
        )
    )


def seed_weather_observation(s: Session, local_day: date, max_temp: float) -> None:
    # Local noon, not local midnight -- keeps the observation unambiguously
    # inside `local_day` after Europe/London bucketing regardless of which
    # side of a UTC/BST offset boundary local midnight itself falls on.
    observed_at = start_of_local_day(local_day) + timedelta(hours=12)
    s.execute(
        text(
            "INSERT INTO weather_observation (source, observed_at, temp) "
            "VALUES (:source, :observed_at, :temp)"
        ),
        {"source": "test", "observed_at": observed_at, "temp": max_temp},
    )


def seed_weather_forecast(s: Session, target_date: date, max_temp: float) -> None:
    s.execute(
        text(
            "INSERT INTO weather_forecast "
            "(id, source, target_date, max_temp, fetched_at) "
            "VALUES (:id, :source, :target_date, :max_temp, :fetched_at)"
        ),
        {
            "id": f"test-forecast-{target_date.isoformat()}",
            "source": "test",
            "target_date": target_date,
            "max_temp": max_temp,
            "fetched_at": datetime(2026, 7, 7, tzinfo=UTC),
        },
    )
