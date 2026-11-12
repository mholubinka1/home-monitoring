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


def _seed_heating_hour(client: MariaDBClient, hour_start: datetime) -> None:
    """A stored hour in which the heating was on."""
    with client.session_write_scope() as session:
        session.add(
            model.heating_hour(
                hour_start=hour_start,
                poll_count=30,
                on_minutes=10,
                source="estimated",
                computed_at=hour_start,
            )
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
    assert today.minutes_below is None


def test_nothing_is_written_when_hive_apps_table_does_not_exist(
    mariadb_client_without_weather_tables: MariaDBClient,
) -> None:
    client = mariadb_client_without_weather_tables

    ComfortDayJob(client).refresh(NOW)

    assert not _comfort_days(client)


def test_todays_row_is_rewritten_on_every_run(mariadb_client: MariaDBClient) -> None:
    _seed_polls(mariadb_client, _at(TODAY), room=18.0)
    job = ComfortDayJob(mariadb_client)
    job.refresh(NOW)

    job.refresh(NOW + timedelta(hours=1))

    today = _comfort_days(mariadb_client)[TODAY]
    assert today.computed_at == datetime(2026, 11, 12, 21, 0)


def test_yesterday_is_settled_on_the_first_run_after_midnight(
    mariadb_client: MariaDBClient,
) -> None:
    # Given yesterday's row was last written yesterday evening
    yesterday = TODAY - timedelta(days=1)
    _seed_polls(mariadb_client, _at(yesterday), room=18.0)
    job = ComfortDayJob(mariadb_client)
    job.refresh(NOW - timedelta(days=1))
    _seed_polls(mariadb_client, _at(TODAY, timedelta(minutes=10)), room=18.0)

    # When the first run after London midnight happens
    after_midnight = datetime(2026, 11, 12, 0, 30, tzinfo=UTC)
    job.refresh(after_midnight)

    # Then yesterday is written one last time, and today has a row
    days = _comfort_days(mariadb_client)
    assert days[yesterday].computed_at == after_midnight.replace(tzinfo=None)
    assert TODAY in days


def test_a_settled_day_is_never_rewritten_when_its_evidence_changes(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a day settled with no comfort temperature, two days ago
    settled = TODAY - timedelta(days=2)
    for day in (settled, TODAY - timedelta(days=1), TODAY):
        _seed_polls(mariadb_client, _at(day), room=18.0)
    job = ComfortDayJob(mariadb_client)
    job.refresh(NOW - timedelta(days=2))
    job.refresh(NOW - timedelta(days=1))

    # When five boosts from before it turn up and the job runs again
    _seed_boosts_on_earlier_days(
        mariadb_client, [16.0, 16.5, 17.0, 17.5, 20.0], before=settled
    )
    job.refresh(NOW)

    # Then the settled day is as it was, while today learns from them
    days = _comfort_days(mariadb_client)
    assert days[settled].comfort_temp is None
    assert days[settled].computed_at == datetime(2026, 11, 11, 20, 0)
    assert days[TODAY].comfort_temp == 17.0


def test_the_first_run_fills_every_day_back_to_the_first_poll_from_its_own_past(
    mariadb_client: MariaDBClient,
) -> None:
    # Given an empty table, and a boost on each of the last ten days: at 20 C ten
    # days ago, one degree cooler each day, down to 11 C yesterday
    _seed_boosts_on_earlier_days(mariadb_client, [float(t) for t in range(11, 21)])
    _seed_polls(mariadb_client, _at(TODAY), room=18.0)

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then all eleven days have a row, each learned only from the boosts before it
    days = _comfort_days(mariadb_client)
    assert sorted(days) == [TODAY - timedelta(days=n) for n in range(10, -1, -1)]
    assert days[TODAY - timedelta(days=6)].comfort_temp is None  # four boosts
    assert days[TODAY - timedelta(days=5)].comfort_temp == 18.0  # 20 19 18 17 16
    assert days[TODAY].comfort_temp == 15.5


def test_with_under_five_boosts_the_last_learned_comfort_temperature_carries_on(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a comfort temperature of 17.0 learned five days ago from boosts that
    # have since left the 90-day window
    learned_on = TODAY - timedelta(days=5)
    _seed_boosts_on_earlier_days(
        mariadb_client,
        [16.0, 16.5, 17.0, 17.5, 20.0],
        before=TODAY - timedelta(days=90),
    )
    _seed_polls(mariadb_client, _at(learned_on), room=18.0)
    job = ComfortDayJob(mariadb_client)
    job.refresh(NOW - timedelta(days=5))
    assert _comfort_days(mariadb_client)[learned_on].comfort_temp == 17.0
    _seed_polls(mariadb_client, _at(TODAY), room=18.0)

    job.refresh(NOW)

    today = _comfort_days(mariadb_client)[TODAY]
    assert today.boost_starts == 0
    assert today.comfort_temp == 17.0


def test_a_day_with_no_thermostat_polls_gets_no_row(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_polls(mariadb_client, _at(TODAY - timedelta(days=2)), room=18.0)
    _seed_polls(mariadb_client, _at(TODAY), room=18.0)

    ComfortDayJob(mariadb_client).refresh(NOW)

    assert sorted(_comfort_days(mariadb_client)) == [TODAY - timedelta(days=2), TODAY]


def test_minutes_below_comfort_are_counted_in_the_counted_hours(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a comfort temperature of 17.0, heating in the 06:00 and 18:00 hours
    # last week, and today ten polls at 16.5 C in the 18:00 hour
    _seed_boosts_on_earlier_days(mariadb_client, [16.0, 16.5, 17.0, 17.5, 20.0])
    last_week = TODAY - timedelta(days=7)
    _seed_heating_hour(mariadb_client, _at(last_week))
    _seed_heating_hour(mariadb_client, _at(last_week, timedelta(hours=6)))
    _seed_polls(mariadb_client, _at(TODAY), room=16.5, count=10)

    ComfortDayJob(mariadb_client).refresh(NOW)

    today = _comfort_days(mariadb_client)[TODAY]
    assert today.counted_hours == "6,18"
    assert today.minutes_below == 20


def test_a_cold_room_outside_the_counted_hours_adds_no_minutes(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_boosts_on_earlier_days(mariadb_client, [16.0, 16.5, 17.0, 17.5, 20.0])
    _seed_heating_hour(mariadb_client, _at(TODAY - timedelta(days=7)))
    _seed_polls(mariadb_client, _at(TODAY, timedelta(hours=3)), room=12.0, count=10)

    ComfortDayJob(mariadb_client).refresh(NOW)

    assert _comfort_days(mariadb_client)[TODAY].minutes_below == 0


def test_a_room_at_the_comfort_temperature_or_unread_adds_no_minutes(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_boosts_on_earlier_days(mariadb_client, [16.0, 16.5, 17.0, 17.5, 20.0])
    _seed_heating_hour(mariadb_client, _at(TODAY - timedelta(days=7)))
    _seed_polls(mariadb_client, _at(TODAY), room=17.0, count=5)
    _seed_polls(mariadb_client, _at(TODAY, timedelta(hours=18, minutes=10)), room=None)

    ComfortDayJob(mariadb_client).refresh(NOW)

    assert _comfort_days(mariadb_client)[TODAY].minutes_below == 0


def test_an_hour_heated_only_before_the_ninety_days_is_not_counted(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_boosts_on_earlier_days(mariadb_client, [16.0, 16.5, 17.0, 17.5, 20.0])
    _seed_heating_hour(mariadb_client, _at(TODAY - timedelta(days=91)))
    _seed_polls(mariadb_client, _at(TODAY), room=16.5, count=10)

    ComfortDayJob(mariadb_client).refresh(NOW)

    today = _comfort_days(mariadb_client)[TODAY]
    assert today.counted_hours == ""
    assert today.minutes_below == 0


def test_counted_hours_follow_london_time_in_summer(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a summer day, with heating at 17:00 UTC (18:00 in London) last week
    summer_day = date(2026, 7, 15)
    five_pm_utc = timedelta(hours=17)
    _seed_boosts_on_earlier_days(
        mariadb_client, [16.0, 16.5, 17.0, 17.5, 20.0], before=summer_day
    )
    _seed_heating_hour(mariadb_client, _at(summer_day - timedelta(days=7), five_pm_utc))
    _seed_polls(mariadb_client, _at(summer_day, five_pm_utc), room=16.5, count=10)

    ComfortDayJob(mariadb_client).refresh(datetime(2026, 7, 15, 20, 0, tzinfo=UTC))

    today = _comfort_days(mariadb_client)[summer_day]
    assert today.counted_hours == "18"
    assert today.minutes_below == 20
