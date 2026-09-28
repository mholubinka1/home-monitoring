from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import responses
from sqlalchemy import text
from sqlalchemy.orm import Session

from octopus_app.data.local_day import start_of_local_day
from octopus_app.data.mysql import model

GRAPHQL_ENDPOINT = "https://api.octopus.energy/v1/graphql/"

# Seven trailing historical days (2026-06-30 .. 2026-07-06, the day before
# as_of's local date in every test using this fixture) with a perfectly
# linear kWh = 20 + 2*heating-degree-days(max_temp) relationship -- HDD uses
# a 15.5C base, so e.g. day1's 15.0C -> HDD 0.5 -> 21.0 kWh, day7's -3.0C ->
# HDD 18.5 -> 57.0 kWh. Flat average of these seven totals is exactly
# 39.0 kWh/day (273.0 / 7) -- the figure #507's unweighted-average fallback
# would use instead.
TRAINING_DAYS_AND_TEMPS = [
    (date(2026, 6, 30), 15.0, "21.0"),
    (date(2026, 7, 1), 12.0, "27.0"),
    (date(2026, 7, 2), 9.0, "33.0"),
    (date(2026, 7, 3), 6.0, "39.0"),
    (date(2026, 7, 4), 3.0, "45.0"),
    (date(2026, 7, 5), 0.0, "51.0"),
    (date(2026, 7, 6), -3.0, "57.0"),
]


def mock_billing_period(start: str, end: str, is_fixed: bool = True) -> None:
    responses.add(
        responses.POST,
        GRAPHQL_ENDPOINT,
        json={"data": {"obtainKrakenToken": {"token": "kraken-jwt-token"}}},
        status=200,
    )
    responses.add(
        responses.POST,
        GRAPHQL_ENDPOINT,
        json={
            "data": {
                "account": {
                    "billingOptions": {
                        "currentBillingPeriodStartDate": start,
                        "currentBillingPeriodEndDate": end,
                        "isFixed": is_fixed,
                    }
                }
            }
        },
        status=200,
    )


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
