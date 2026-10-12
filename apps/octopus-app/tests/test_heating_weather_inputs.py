from datetime import UTC, date, datetime, timedelta

import pytest

from common import local_day
from common.local_day import set_local_timezone
from octopus_app.data.mysql.client import MariaDBClient, weather_observation_table

HOME = "51.40,-0.05"


def _seed_hours(
    client: MariaDBClient,
    location: str,
    first_stamp: datetime,
    temps: list[float | None],
    source: str = "open-meteo",
) -> None:
    """One weather_observation row per temperature, an hour apart from
    first_stamp (each stamp holds the hour ending at it)."""
    with client.session_write_scope() as session:
        for i, temp in enumerate(temps):
            # Through the typed table so the datetime is stored as a naive UTC
            # value in the column's own format, as hive-app writes it.
            session.execute(
                weather_observation_table.insert().values(
                    source=source,
                    location=location,
                    observed_at=first_stamp + timedelta(hours=i),
                    temp=temp,
                )
            )


# pylint: disable-next=too-many-arguments,too-many-positional-arguments
def _seed_day(
    client: MariaDBClient,
    day: date,
    temp: float,
    location: str = HOME,
    source: str = "open-meteo",
    skip: int | None = None,
) -> None:
    """A full local day of identical readings, optionally leaving one hour out."""
    for i, stamp in enumerate(local_day.hour_end_stamps(day)):
        if i != skip:
            _seed_hours(client, location, stamp, [temp], source)


def test_a_local_day_with_every_hour_present_has_its_mean_temperature(
    mariadb_client: MariaDBClient,
) -> None:
    # 2026-01-06 is GMT: its 24 hour-ends are 01:00Z on the 6th to 00:00Z on
    # the 7th. Temperatures 1.0 .. 24.0 average 12.5.
    _seed_hours(
        mariadb_client,
        HOME,
        datetime(2026, 1, 6, 1, tzinfo=UTC),
        [float(t) for t in range(1, 25)],
    )

    means = mariadb_client.read_complete_day_mean_temperatures(
        date(2026, 1, 6), date(2026, 1, 6)
    )

    assert means == {date(2026, 1, 6): 12.5}


def test_a_day_with_one_hour_missing_is_absent(mariadb_client: MariaDBClient) -> None:
    day = date(2026, 1, 6)
    _seed_day(mariadb_client, day, 10.0, skip=7)

    assert mariadb_client.read_complete_day_mean_temperatures(day, day) == {}


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf")])
def test_an_hour_with_no_usable_temperature_counts_as_missing(
    mariadb_client: MariaDBClient, bad: float | None
) -> None:
    day = date(2026, 1, 6)
    stamps = local_day.hour_end_stamps(day)
    _seed_hours(mariadb_client, HOME, stamps[0], [10.0] * 23 + [bad])

    assert mariadb_client.read_complete_day_mean_temperatures(day, day) == {}


def test_a_day_entirely_under_an_older_location_is_ignored(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_day(mariadb_client, date(2026, 1, 5), 3.0, location="55.00,-1.00")
    _seed_day(mariadb_client, date(2026, 1, 6), 10.0)

    means = mariadb_client.read_complete_day_mean_temperatures(
        date(2026, 1, 5), date(2026, 1, 6)
    )

    assert means == {date(2026, 1, 6): 10.0}


def test_when_the_newest_row_has_a_new_location_the_old_locations_days_are_gone(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_day(mariadb_client, date(2026, 1, 5), 3.0, location="55.00,-1.00")
    _seed_day(mariadb_client, date(2026, 1, 6), 10.0)
    newest = local_day.hour_end_stamps(date(2026, 1, 7))[0]
    _seed_hours(mariadb_client, "60.00,-2.00", newest, [1.0])

    means = mariadb_client.read_complete_day_mean_temperatures(
        date(2026, 1, 5), date(2026, 1, 6)
    )

    assert means == {}


def test_no_observations_at_all_gives_no_days(mariadb_client: MariaDBClient) -> None:
    means = mariadb_client.read_complete_day_mean_temperatures(
        date(2026, 1, 5), date(2026, 1, 6)
    )

    assert means == {}


def test_clock_change_days_are_complete_with_their_own_hour_count(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_day(mariadb_client, date(2026, 3, 29), 8.0)  # 23 hours
    _seed_day(mariadb_client, date(2026, 10, 25), 9.0)  # 25 hours

    means = mariadb_client.read_complete_day_mean_temperatures(
        date(2026, 3, 29), date(2026, 10, 25)
    )

    assert means == {date(2026, 3, 29): 8.0, date(2026, 10, 25): 9.0}


def test_a_23_hour_day_with_a_stray_24th_row_uses_only_the_days_own_stamps(
    mariadb_client: MariaDBClient,
) -> None:
    day = date(2026, 3, 29)
    _seed_day(mariadb_client, day, 8.0)
    # One hour past the day's last stamp belongs to the next day.
    after = local_day.hour_end_stamps(day)[-1] + timedelta(hours=1)
    _seed_hours(mariadb_client, HOME, after, [99.0])

    assert mariadb_client.read_complete_day_mean_temperatures(day, day) == {day: 8.0}


def test_a_live_reading_wins_over_an_archive_one_for_the_same_hour(
    mariadb_client: MariaDBClient,
) -> None:
    day = date(2026, 1, 6)
    _seed_day(mariadb_client, day, 10.0)
    _seed_day(mariadb_client, day, 20.0, source="open-meteo-archive")

    assert mariadb_client.read_complete_day_mean_temperatures(day, day) == {day: 10.0}


def test_an_hour_with_only_an_archive_reading_uses_it(
    mariadb_client: MariaDBClient,
) -> None:
    day = date(2026, 1, 6)
    _seed_day(mariadb_client, day, 10.0, skip=0)
    first = local_day.hour_end_stamps(day)[0]
    _seed_hours(mariadb_client, HOME, first, [34.0], source="open-meteo-archive")

    # 23 live hours of 10.0 and one archive hour of 34.0.
    assert mariadb_client.read_complete_day_mean_temperatures(day, day) == {day: 11.0}


def test_a_day_is_complete_when_live_and_archive_together_cover_every_hour(
    mariadb_client: MariaDBClient,
) -> None:
    day = date(2026, 1, 6)
    _seed_day(mariadb_client, day, 10.0, skip=3)
    _seed_day(mariadb_client, day, 10.0, source="open-meteo-archive", skip=5)

    assert mariadb_client.read_complete_day_mean_temperatures(day, day) == {day: 10.0}


def test_with_new_york_configured_days_follow_new_york_stamps(
    mariadb_client: MariaDBClient,
) -> None:
    day = date(2026, 1, 6)
    set_local_timezone("America/New_York")
    _seed_day(mariadb_client, day, 5.0)

    assert mariadb_client.read_complete_day_mean_temperatures(day, day) == {day: 5.0}

    # The same rows are not a complete London day.
    set_local_timezone("Europe/London")
    assert mariadb_client.read_complete_day_mean_temperatures(day, day) == {}


def test_missing_weather_tables_give_no_days(
    mariadb_client_without_weather_tables: MariaDBClient,
) -> None:
    client = mariadb_client_without_weather_tables

    assert (
        client.read_complete_day_mean_temperatures(date(2026, 1, 5), date(2026, 1, 6))
        == {}
    )
