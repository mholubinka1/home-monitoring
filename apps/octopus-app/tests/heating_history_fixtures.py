"""Seeds a planted heating history: weather and gas whose true model is known.

The true model: gas = baseload + slope * degree-days below THRESHOLD of an
effective temperature blended WEIGHT from yesterday, plus small deterministic
noise. A fit on it should recover these numbers."""

import math
import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from common import local_day
from octopus_app.data.model import ConsumptionSummary, Energy
from octopus_app.data.mysql.client import (
    MariaDBClient,
    heating_status_table,
    weather_forecast_table,
    weather_observation_table,
)

PLANTED_THRESHOLD = 12.0
PLANTED_SLOPE = 3.0
PLANTED_WEIGHT = 0.4
PLANTED_BASELOAD = 7.0
HOME = "51.40,-0.05"


def planted_means(first_day: date, days: int) -> dict[date, float]:
    """A seasonal daily mean temperature: coldest in mid-January (about 1.5 C),
    warmest in mid-July (about 19.5 C), with a little noise."""
    noise = random.Random(7)
    return {
        first_day
        + timedelta(days=i): 10.5
        - 9.0
        * math.cos(
            2
            * math.pi
            * ((first_day + timedelta(days=i)).timetuple().tm_yday - 15)
            / 365
        )
        + noise.uniform(-2.0, 2.0)
        for i in range(days)
    }


def planted_gas(means: dict[date, float]) -> dict[date, float]:
    """The gas each day with a complete mean and a previous day's mean uses."""
    noise = random.Random(11)
    gas = {}
    for day in sorted(means):
        yesterday = means.get(day - timedelta(days=1))
        if yesterday is None:
            continue
        effective = (1 - PLANTED_WEIGHT) * means[day] + PLANTED_WEIGHT * yesterday
        gas[day] = (
            PLANTED_BASELOAD
            + PLANTED_SLOPE * max(0.0, PLANTED_THRESHOLD - effective)
            + noise.uniform(-0.8, 0.8)
        )
    return gas


def seed_planted_history(
    client: MariaDBClient, *, last_day: date, days: int
) -> tuple[dict[date, float], dict[date, float]]:
    """Hourly weather for every day and a gas summary for every day but the
    first, ending on last_day. Returns the (means, gas) that were seeded."""
    means = planted_means(last_day - timedelta(days=days - 1), days)
    gas = planted_gas(means)
    with client.session_write_scope() as session:
        session.execute(
            weather_observation_table.insert(),
            [
                {
                    "source": "open-meteo",
                    "location": HOME,
                    "observed_at": stamp.astimezone(UTC).replace(tzinfo=None),
                    "temp": mean,
                }
                for day, mean in means.items()
                for stamp in local_day.hour_end_stamps(day)
            ],
        )
    client.write_consumption_summary(
        [
            ConsumptionSummary(
                Energy.gas,
                day,
                Decimal(f"{total:.5f}"),
                local_day.expected_half_hour_count(day),
            )
            for day, total in gas.items()
        ]
    )
    return means, gas


def seed_forecast_means(client: MariaDBClient, means: dict[date, float]) -> None:
    """One weather_forecast row per day with the given forecast mean."""
    with client.session_write_scope() as session:
        session.execute(
            weather_forecast_table.insert(),
            [
                {
                    "id": f"test-forecast-{day.isoformat()}",
                    "source": "test",
                    "target_date": day,
                    "max_temp": mean + 3.0,
                    "mean_temp": mean,
                    "fetched_at": datetime(2026, 10, 13, tzinfo=UTC).replace(
                        tzinfo=None
                    ),
                }
                for day, mean in means.items()
            ],
        )


def effective_of(means: dict[date, float], day: date) -> float:
    """The planted effective temperature of a day."""
    return (1 - PLANTED_WEIGHT) * means[day] + PLANTED_WEIGHT * means[
        day - timedelta(days=1)
    ]


def seed_gas_day(client: MariaDBClient, day: date, kwh: float) -> None:
    """One complete gas day."""
    client.write_consumption_summary(
        [
            ConsumptionSummary(
                Energy.gas,
                day,
                Decimal(f"{kwh:.5f}"),
                local_day.expected_half_hour_count(day),
            )
        ]
    )


def seed_weather_day(client: MariaDBClient, day: date, mean: float) -> None:
    """Hourly weather for one day, all at the given mean."""
    with client.session_write_scope() as session:
        session.execute(
            weather_observation_table.insert(),
            [
                {
                    "source": "open-meteo",
                    "location": HOME,
                    "observed_at": stamp.astimezone(UTC).replace(tzinfo=None),
                    "temp": mean,
                }
                for stamp in local_day.hour_end_stamps(day)
            ],
        )


def seed_holiday(client: MariaDBClient, first_day: date, days: int, kwh: float) -> None:
    """A stretch of away days: near-zero but non-zero gas."""
    for offset in range(days):
        seed_gas_day(client, first_day + timedelta(days=offset), kwh)


def seed_heating_readings(
    client: MariaDBClient, heating: dict[date, bool], per_day: int = 720
) -> None:
    """A full day of two-minute heating_status polls for each given day: demand
    ('ON') all day on a heating day, none on a non-heating day."""
    rows = [
        {
            "polled_at": (local_day.start_of_local_day(day) + timedelta(minutes=2 * i))
            .astimezone(UTC)
            .replace(tzinfo=None),
            "state": "ON" if wanted else "OFF",
            "boost_active": False,
        }
        for day, wanted in heating.items()
        for i in range(per_day)
    ]
    with client.session_write_scope() as session:
        session.execute(heating_status_table.insert(), rows)
