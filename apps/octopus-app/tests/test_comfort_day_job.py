from datetime import UTC, date, datetime, timedelta

from octopus_app.data.comfort_job import ComfortDayJob
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient, heating_status_table

TODAY = date(2026, 11, 12)  # London is on GMT, so local time is UTC
NOW = datetime(2026, 11, 12, 20, 0, tzinfo=UTC)
EVENING = timedelta(hours=18)


def _at(day: date, offset: timedelta = EVENING) -> datetime:
    return datetime(day.year, day.month, day.day) + offset


def _seed_polls(
    client: MariaDBClient,
    start: datetime,
    *,
    room: float | None,
    count: int = 1,
    boost: bool = False,
) -> None:
    """`count` two-minute polls from `start` with the room at `room`."""
    rows = [
        {
            "polled_at": start + timedelta(minutes=2 * i),
            "current_temp": room,
            "target_temp": 20.0 if boost else 7.0,
            "mode": "SCHEDULE",
            "state": "ON" if boost else "OFF",
            "boost_active": boost,
        }
        for i in range(count)
    ]
    with client.session_write_scope() as session:
        session.execute(heating_status_table.insert(), rows)


def _seed_boosts_on_earlier_days(
    client: MariaDBClient, rooms: list[float], *, before: date = TODAY
) -> None:
    """One boost on each of the days before `before`, newest first, starting
    with the room at each of `rooms`."""
    for days_ago, room in enumerate(rooms, start=1):
        _seed_polls(
            client, _at(before - timedelta(days=days_ago)), room=room, boost=True
        )


def _comfort_days(client: MariaDBClient) -> dict[date, model.comfort_day]:
    with client.session_read_scope() as session:
        return {row.date: row for row in session.query(model.comfort_day)}


def test_the_comfort_temperature_is_the_median_room_temperature_at_boost_starts(
    mariadb_client: MariaDBClient,
) -> None:
    # Given five boosts on earlier days that started at these room temperatures
    _seed_boosts_on_earlier_days(mariadb_client, [16.0, 16.5, 17.0, 17.5, 20.0])
    _seed_polls(mariadb_client, _at(TODAY), room=18.0)

    # When the job runs
    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then today's comfort temperature is their median
    today = _comfort_days(mariadb_client)[TODAY]
    assert today.comfort_temp == 17.0
    assert today.boost_starts == 5


def test_a_boost_today_is_not_evidence_for_today(
    mariadb_client: MariaDBClient,
) -> None:
    # Given five earlier boosts, and a much warmer boost earlier today
    _seed_boosts_on_earlier_days(mariadb_client, [16.0, 16.5, 17.0, 17.5, 20.0])
    _seed_polls(mariadb_client, _at(TODAY), room=25.0, boost=True)

    ComfortDayJob(mariadb_client).refresh(NOW)

    today = _comfort_days(mariadb_client)[TODAY]
    assert today.comfort_temp == 17.0
    assert today.boost_starts == 5


def test_boost_polls_within_ten_minutes_are_one_boost_and_a_longer_gap_starts_another(
    mariadb_client: MariaDBClient,
) -> None:
    # Given four earlier one-poll boosts, and yesterday a boost of six polls that
    # warmed the room from 15.0, a poll 10 minutes after its last, and another
    # boost 12 minutes after that
    _seed_boosts_on_earlier_days(
        mariadb_client, [18.0, 18.0, 18.0, 18.0], before=TODAY - timedelta(days=1)
    )
    yesterday = _at(TODAY - timedelta(days=1))
    for i in range(6):
        _seed_polls(
            mariadb_client,
            yesterday + timedelta(minutes=2 * i),
            room=15.0 + i,
            boost=True,
        )
    _seed_polls(
        mariadb_client, yesterday + timedelta(minutes=20), room=21.0, boost=True
    )
    _seed_polls(
        mariadb_client, yesterday + timedelta(minutes=32), room=30.0, boost=True
    )
    _seed_polls(mariadb_client, _at(TODAY), room=18.0)

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then yesterday gave two boost starts, at 15.0 and 30.0: six in all
    today = _comfort_days(mariadb_client)[TODAY]
    assert today.boost_starts == 6
    assert today.comfort_temp == 18.0


def test_a_boost_more_than_ninety_days_ago_is_not_used(
    mariadb_client: MariaDBClient,
) -> None:
    # Given five boosts in the window and a cold one 91 days ago
    _seed_boosts_on_earlier_days(mariadb_client, [16.0, 16.5, 17.0, 17.5, 20.0])
    _seed_polls(mariadb_client, _at(TODAY - timedelta(days=91)), room=5.0, boost=True)
    _seed_polls(mariadb_client, _at(TODAY), room=18.0)

    ComfortDayJob(mariadb_client).refresh(NOW)

    today = _comfort_days(mariadb_client)[TODAY]
    assert today.boost_starts == 5
    assert today.comfort_temp == 17.0


def test_with_under_five_boosts_and_nothing_learned_before_there_is_no_comfort_temperature(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_boosts_on_earlier_days(mariadb_client, [16.0, 16.5, 17.0, 17.5])
    _seed_polls(mariadb_client, _at(TODAY), room=18.0)

    ComfortDayJob(mariadb_client).refresh(NOW)

    today = _comfort_days(mariadb_client)[TODAY]
    assert today.comfort_temp is None
    assert today.boost_starts == 4


def test_nothing_is_written_when_hive_apps_table_does_not_exist(
    mariadb_client_without_weather_tables: MariaDBClient,
) -> None:
    client = mariadb_client_without_weather_tables

    ComfortDayJob(client).refresh(NOW)

    assert not _comfort_days(client)
