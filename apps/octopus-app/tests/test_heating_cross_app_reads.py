import math
from datetime import date, datetime

from octopus_app.data.mysql.client import (
    MariaDBClient,
    weather_forecast_table,
)


def _seed_forecast(
    client: MariaDBClient, target_date: date, mean_temp: float | None
) -> None:
    with client.session_write_scope() as session:
        session.execute(
            weather_forecast_table.insert().values(
                id=f"test-{target_date.isoformat()}",
                source="test",
                target_date=target_date,
                max_temp=20.0,
                mean_temp=mean_temp,
                fetched_at=datetime(2026, 10, 14),
            )
        )


def test_forecast_mean_temperatures_are_read_for_the_inclusive_range(
    mariadb_client: MariaDBClient,
) -> None:
    for day, mean in [(14, 9.0), (15, 8.5), (16, 7.0), (17, 6.0)]:
        _seed_forecast(mariadb_client, date(2026, 10, day), mean)

    means = mariadb_client.read_weather_forecast_mean_temps(
        date(2026, 10, 15), date(2026, 10, 16)
    )

    assert means == {date(2026, 10, 15): 8.5, date(2026, 10, 16): 7.0}


def test_a_missing_or_non_finite_forecast_mean_is_treated_as_no_forecast(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_forecast(mariadb_client, date(2026, 10, 15), None)
    _seed_forecast(mariadb_client, date(2026, 10, 16), math.nan)
    _seed_forecast(mariadb_client, date(2026, 10, 17), math.inf)
    _seed_forecast(mariadb_client, date(2026, 10, 18), 5.0)

    means = mariadb_client.read_weather_forecast_mean_temps(
        date(2026, 10, 15), date(2026, 10, 18)
    )

    assert means == {date(2026, 10, 18): 5.0}


def test_forecast_mean_temperatures_are_empty_when_hive_app_has_never_run(
    mariadb_client_without_weather_tables: MariaDBClient,
) -> None:
    means = mariadb_client_without_weather_tables.read_weather_forecast_mean_temps(
        date(2026, 10, 15), date(2026, 10, 16)
    )

    assert means == {}
