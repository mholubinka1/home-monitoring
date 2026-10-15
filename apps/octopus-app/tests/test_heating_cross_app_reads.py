import math
from datetime import UTC, date, datetime

from octopus_app.data.mysql.client import (
    MariaDBClient,
    heating_status_table,
    weather_forecast_table,
)
from octopus_app.data.thermostat_check import HeatingReading


def _seed_poll(
    client: MariaDBClient,
    polled_at: datetime,
    state: str | None = "ON",
    boost_active: bool | None = False,
) -> None:
    # Stored as hive-app writes it: naive UTC.
    with client.session_write_scope() as session:
        session.execute(
            heating_status_table.insert().values(
                polled_at=polled_at.replace(tzinfo=None),
                state=state,
                boost_active=boost_active,
            )
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


def test_heating_polls_in_the_window_are_read_back_in_time_order(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_poll(mariadb_client, datetime(2026, 10, 1, 9, 0, tzinfo=UTC), "OFF")
    _seed_poll(mariadb_client, datetime(2026, 10, 1, 8, 0, tzinfo=UTC), "ON", True)

    readings = mariadb_client.read_heating_readings(
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
    )

    assert readings == [
        HeatingReading(datetime(2026, 10, 1, 8, 0, tzinfo=UTC), "ON", True),
        HeatingReading(datetime(2026, 10, 1, 9, 0, tzinfo=UTC), "OFF", False),
    ]


def test_a_poll_at_the_window_start_is_in_and_one_at_the_end_is_out(
    mariadb_client: MariaDBClient,
) -> None:
    start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    end = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
    _seed_poll(mariadb_client, datetime(2026, 9, 30, 23, 59, tzinfo=UTC))
    _seed_poll(mariadb_client, start)
    _seed_poll(mariadb_client, end)

    readings = mariadb_client.read_heating_readings(start, end)

    assert [reading.polled_at for reading in readings] == [start]


def test_a_poll_with_no_state_or_boost_flag_reads_as_empty_and_not_boosted(
    mariadb_client: MariaDBClient,
) -> None:
    polled_at = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    _seed_poll(mariadb_client, polled_at, state=None, boost_active=None)

    readings = mariadb_client.read_heating_readings(
        datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 2, tzinfo=UTC)
    )

    assert readings == [HeatingReading(polled_at, "", False)]


def test_heating_polls_are_empty_when_hive_app_has_never_run(
    mariadb_client_without_weather_tables: MariaDBClient,
) -> None:
    readings = mariadb_client_without_weather_tables.read_heating_readings(
        datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 2, tzinfo=UTC)
    )

    assert readings == []


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
