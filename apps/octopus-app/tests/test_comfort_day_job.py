# pylint: disable=too-many-lines
from datetime import UTC, date, datetime, timedelta

from octopus_app.data.comfort_job import ComfortDayJob
from octopus_app.data.model import ComfortDayRecord
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import (
    MariaDBClient,
    heating_status_table,
    presence_period_table,
)

TODAY = date(2026, 11, 12)  # London is on GMT, so local time is UTC
NOW = datetime(2026, 11, 12, 20, 0, tzinfo=UTC)
EVENING = timedelta(hours=18)


def _at(day: date, offset: timedelta = EVENING) -> datetime:
    return datetime(day.year, day.month, day.day) + offset


def _seed_polls(  # pylint: disable=too-many-arguments
    client: MariaDBClient,
    start: datetime,
    *,
    room: float | None,
    count: int = 1,
    boost: bool = False,
    target: float | None = None,
    on: bool = False,
    online: bool = True,
) -> None:
    """`count` two-minute polls from `start` with the room at `room`; Hive's
    target is `target`, else 20.0 in a boost and the 7.0 frost setting outside;
    the heating is on in a boost or when `on`; `online` is whether Hive could
    reach the thermostat."""
    rows = [
        {
            "polled_at": start + timedelta(minutes=2 * i),
            "current_temp": room,
            "target_temp": target if target is not None else (20.0 if boost else 7.0),
            "mode": "SCHEDULE",
            "state": "ON" if boost or on else "OFF",
            "boost_active": boost,
            "online": online,
        }
        for i in range(count)
    ]
    with client.session_write_scope() as session:
        session.execute(heating_status_table.insert(), rows)


def _seed_stored_comfort(
    client: MariaDBClient, day: date, comfort_temp: float | None
) -> None:
    """A comfort_day row for `day`, settled as the old rule left it (no quiet
    count), at `comfort_temp`."""
    client.write_comfort_days(
        [
            ComfortDayRecord(
                date=day,
                comfort_temp=comfort_temp,
                boost_starts=0,
                counted_hours="",
                minutes_below=None,
                computed_at=_at(day + timedelta(days=1), timedelta(0)),
            )
        ]
    )


def _seed_stepped_comfort(  # pylint: disable=too-many-arguments
    client: MariaDBClient,
    day: date,
    comfort_temp: float,
    *,
    quiet_count: int = 0,
    raised_at: datetime | None = None,
    day_kind: str | None = None,
) -> None:
    """A comfort_day row for `day`, settled by the stepped rule."""
    client.write_comfort_days(
        [
            ComfortDayRecord(
                date=day,
                comfort_temp=comfort_temp,
                boost_starts=0,
                counted_hours="",
                minutes_below=None,
                computed_at=_at(day + timedelta(days=1), timedelta(0)),
                raised_at=raised_at,
                day_kind=day_kind,
                quiet_count=quiet_count,
            )
        ]
    )


def _seed_heating_hour(client: MariaDBClient, hour_start: datetime) -> None:
    """A stored half-hour, the second of the hour, in which the heating was on."""
    starts_at = hour_start + timedelta(minutes=30)
    with client.session_write_scope() as session:
        session.add(
            model.heating_interval(
                starts_at=starts_at,
                ends_at=starts_at + timedelta(minutes=30),
                poll_count=15,
                on_minutes=10,
                source="estimated",
                computed_at=starts_at,
            )
        )


def _comfort_days(client: MariaDBClient) -> dict[date, model.comfort_day]:
    with client.session_read_scope() as session:
        return {row.date: row for row in session.query(model.comfort_day)}


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
    # Given a day settled two days ago
    settled = TODAY - timedelta(days=2)
    for day in (settled, TODAY - timedelta(days=1), TODAY):
        _seed_polls(mariadb_client, _at(day), room=18.0)
    job = ComfortDayJob(mariadb_client)
    job.refresh(NOW - timedelta(days=2))
    job.refresh(NOW - timedelta(days=1))

    # When a boost on that day turns up and the job runs again
    _seed_polls(
        mariadb_client, _at(settled, timedelta(hours=19)), room=16.0, boost=True
    )
    job.refresh(NOW)

    # Then the settled day is as it was
    days = _comfort_days(mariadb_client)
    assert days[settled].computed_at == datetime(2026, 11, 11, 20, 0)
    assert days[settled].boost_starts == 0


def test_the_first_run_fills_every_day_back_to_the_first_poll(
    mariadb_client: MariaDBClient,
) -> None:
    # Given an empty table, and polls on each of the last ten days and today
    for days_ago in range(10, -1, -1):
        _seed_polls(mariadb_client, _at(TODAY - timedelta(days=days_ago)), room=18.0)

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then all eleven days have a row
    days = _comfort_days(mariadb_client)
    assert sorted(days) == [TODAY - timedelta(days=n) for n in range(10, -1, -1)]


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
    # Given yesterday's stored 17.0, heating in the 06:00 and 18:00 hours
    # last week, and today ten polls at 16.4 C in the 18:00 hour
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    last_week = TODAY - timedelta(days=7)
    _seed_heating_hour(mariadb_client, _at(last_week))
    _seed_heating_hour(mariadb_client, _at(last_week, timedelta(hours=6)))
    _seed_polls(mariadb_client, _at(TODAY), room=16.4, count=10)

    ComfortDayJob(mariadb_client).refresh(NOW)

    today = _comfort_days(mariadb_client)[TODAY]
    assert today.counted_hours == "6,18"
    assert today.minutes_below == 20


def test_a_cold_room_outside_the_counted_hours_adds_no_minutes(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    _seed_heating_hour(mariadb_client, _at(TODAY - timedelta(days=7)))
    _seed_polls(mariadb_client, _at(TODAY, timedelta(hours=3)), room=12.0, count=10)

    ComfortDayJob(mariadb_client).refresh(NOW)

    assert _comfort_days(mariadb_client)[TODAY].minutes_below == 0


def test_a_room_at_the_comfort_temperature_or_unread_adds_no_minutes(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    _seed_heating_hour(mariadb_client, _at(TODAY - timedelta(days=7)))
    _seed_polls(mariadb_client, _at(TODAY), room=17.0, count=5)
    _seed_polls(mariadb_client, _at(TODAY, timedelta(hours=18, minutes=10)), room=None)

    ComfortDayJob(mariadb_client).refresh(NOW)

    assert _comfort_days(mariadb_client)[TODAY].minutes_below == 0


def test_an_hour_heated_last_season_is_counted_and_one_over_a_year_ago_is_not(
    mariadb_client: MariaDBClient,
) -> None:
    # Given heating in the 18:00 hour 91 days ago and the 06:00 hour 366 days ago
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    # (91 days ago was in British Summer Time: 17:00 UTC is 18:00 in London)
    _seed_heating_hour(
        mariadb_client, _at(TODAY - timedelta(days=91), timedelta(hours=17))
    )
    _seed_heating_hour(
        mariadb_client, _at(TODAY - timedelta(days=366), timedelta(hours=6))
    )
    _seed_polls(mariadb_client, _at(TODAY), room=16.4, count=10)

    ComfortDayJob(mariadb_client).refresh(NOW)

    today = _comfort_days(mariadb_client)[TODAY]
    assert today.counted_hours == "18"
    assert today.minutes_below == 20


def test_counted_hours_follow_london_time_in_summer(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a summer day, with heating at 17:00 UTC (18:00 in London) last week
    summer_day = date(2026, 7, 15)
    five_pm_utc = timedelta(hours=17)
    _seed_stored_comfort(mariadb_client, summer_day - timedelta(days=1), 17.0)
    _seed_heating_hour(mariadb_client, _at(summer_day - timedelta(days=7), five_pm_utc))
    _seed_polls(mariadb_client, _at(summer_day, five_pm_utc), room=16.4, count=10)

    ComfortDayJob(mariadb_client).refresh(datetime(2026, 7, 15, 20, 0, tzinfo=UTC))

    today = _comfort_days(mariadb_client)[summer_day]
    assert today.counted_hours == "18"
    assert today.minutes_below == 20


def test_a_settled_day_stays_settled_while_no_later_day_has_polls(
    mariadb_client: MariaDBClient,
) -> None:
    # Given yesterday was settled after midnight, and Hive has sent nothing today
    yesterday = TODAY - timedelta(days=1)
    _seed_polls(mariadb_client, _at(yesterday), room=18.0)
    job = ComfortDayJob(mariadb_client)
    settled_at = datetime(2026, 11, 12, 0, 30, tzinfo=UTC)
    job.refresh(settled_at)

    # When a boost on yesterday turns up and the job runs again
    _seed_polls(
        mariadb_client, _at(yesterday, timedelta(hours=19)), room=16.0, boost=True
    )
    job.refresh(NOW)

    row = _comfort_days(mariadb_client)[yesterday]
    assert row.computed_at == settled_at.replace(tzinfo=None)
    assert row.boost_starts == 0


def _seed_presence_period(
    client: MariaDBClient,
    state: str,
    started_at: datetime,
    ended_at: datetime | None = None,
) -> None:
    """A Presence Period as hive-app stores it; ended_at None is the open one."""
    with client.session_write_scope() as session:
        session.execute(
            presence_period_table.insert(),
            {
                "state": state,
                "started_at": started_at,
                "ended_at": ended_at,
                "phones_seen": 0,
            },
        )


def _seed_cold_evening(client: MariaDBClient, day: date = TODAY) -> None:
    """A comfort temperature of 17.0, the 18:00 hour counted, and on `day` ten
    polls at 16.4 C from 18:00 to 18:18."""
    _seed_stored_comfort(client, day - timedelta(days=1), 17.0)
    _seed_heating_hour(client, _at(day - timedelta(days=7)))
    _seed_polls(client, _at(day), room=16.4, count=10)


def test_minutes_while_no_one_is_home_are_left_out_of_minutes_below(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a cold evening, and no one home from 18:06 to 18:12
    _seed_cold_evening(mariadb_client)
    _seed_presence_period(
        mariadb_client,
        "no_one_home",
        _at(TODAY, timedelta(hours=18, minutes=6)),
        _at(TODAY, timedelta(hours=18, minutes=12)),
    )

    # When the Comfort Day is settled
    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then the polls at 18:06, 18:08 and 18:10 add nothing, while 18:12, when the
    # period ended, counts: 7 polls, 14 minutes
    assert _comfort_days(mariadb_client)[TODAY].minutes_below == 14


def test_an_absence_that_began_before_the_day_leaves_out_its_minutes_on_the_day(
    mariadb_client: MariaDBClient,
) -> None:
    # Given yesterday is settled, a cold evening today, and no one home from 22:00
    # yesterday until 18:10 today
    _seed_cold_evening(mariadb_client)
    job = ComfortDayJob(mariadb_client)
    job.refresh(datetime(2026, 11, 12, 0, 30, tzinfo=UTC))
    _seed_presence_period(
        mariadb_client,
        "no_one_home",
        _at(TODAY - timedelta(days=1), timedelta(hours=22)),
        _at(TODAY, timedelta(hours=18, minutes=10)),
    )

    # When today is written
    job.refresh(NOW)

    # Then only the polls from 18:10 count: 5 polls, 10 minutes
    assert _comfort_days(mariadb_client)[TODAY].minutes_below == 10


def test_an_open_no_one_home_period_leaves_out_every_minute_from_its_start(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a cold evening, and no one home since 18:10 and still
    _seed_cold_evening(mariadb_client)
    _seed_presence_period(
        mariadb_client, "no_one_home", _at(TODAY, timedelta(hours=18, minutes=10))
    )

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then only the five polls before 18:10 count
    assert _comfort_days(mariadb_client)[TODAY].minutes_below == 10


def test_minutes_while_someone_is_home_or_presence_is_unknown_still_count(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a cold evening, someone home until 18:10 and presence unknown since
    _seed_cold_evening(mariadb_client)
    _seed_presence_period(
        mariadb_client,
        "someone_home",
        _at(TODAY),
        _at(TODAY, timedelta(hours=18, minutes=10)),
    )
    _seed_presence_period(
        mariadb_client, "unknown", _at(TODAY, timedelta(hours=18, minutes=10))
    )

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then all ten polls count
    assert _comfort_days(mariadb_client)[TODAY].minutes_below == 20


def test_every_minute_counts_when_hive_apps_presence_table_does_not_exist(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a cold evening, and hive-app has never created presence_period
    _seed_cold_evening(mariadb_client)
    with mariadb_client.session_write_scope() as session:
        presence_period_table.drop(session.connection())

    ComfortDayJob(mariadb_client).refresh(NOW)

    assert _comfort_days(mariadb_client)[TODAY].minutes_below == 20


def test_a_day_settled_before_presence_rows_existed_is_unchanged(
    mariadb_client: MariaDBClient,
) -> None:
    # Given yesterday's cold evening was settled after midnight
    yesterday = TODAY - timedelta(days=1)
    _seed_cold_evening(mariadb_client, yesterday)
    job = ComfortDayJob(mariadb_client)
    settled_at = datetime(2026, 11, 12, 0, 30, tzinfo=UTC)
    job.refresh(settled_at)

    # When a no_one_home period covering that evening appears and the job runs
    _seed_presence_period(
        mariadb_client,
        "no_one_home",
        _at(yesterday),
        _at(yesterday, timedelta(hours=19)),
    )
    job.refresh(NOW)

    # Then yesterday is as it was settled
    row = _comfort_days(mariadb_client)[yesterday]
    assert row.computed_at == settled_at.replace(tzinfo=None)
    assert row.minutes_below == 20


def test_with_no_earlier_row_the_first_day_starts_at_the_floor(
    mariadb_client: MariaDBClient,
) -> None:
    # Given no comfort_day row at all, and a poll today
    _seed_polls(mariadb_client, _at(TODAY), room=18.0)

    # When the first day is written
    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then it starts at the 16 C floor
    assert _comfort_days(mariadb_client)[TODAY].comfort_temp == 16.0


def test_the_first_day_after_the_change_starts_from_the_old_value_rounded_up(
    mariadb_client: MariaDBClient,
) -> None:
    # Given yesterday's row was settled by the old rule at 16.8
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 16.8)
    _seed_polls(mariadb_client, _at(TODAY), room=18.0)

    # When today is written
    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then today starts at 17.0 with no Quiet Days counted
    today = _comfort_days(mariadb_client)[TODAY]
    assert today.comfort_temp == 17.0
    assert today.quiet_count == 0


HELD = timedelta(hours=19)  # inside the weekday Comfort Window (07:30 to 23:00)


def _seed_held_boost(
    client: MariaDBClient,
    day: date,
    *,
    comfort: float = 17.0,
    room: float = 16.7,
    at: timedelta = HELD,
) -> None:
    """Hive holding `comfort` for ten minutes, then a boost started with the room
    at `room`, `at` into `day`."""
    _seed_polls(
        client,
        _at(day, at - timedelta(minutes=10)),
        room=comfort,
        count=5,
        target=comfort,
    )
    _seed_polls(client, _at(day, at), room=room, count=3, boost=True)


def test_a_held_boost_raises_the_day_and_the_next_day_starts_raised(
    mariadb_client: MariaDBClient,
) -> None:
    # Given today holds 17.0, and a boost starts in the window at a room of 16.7
    # with Hive's target at 17.0 just before it
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    _seed_held_boost(mariadb_client, TODAY)
    job = ComfortDayJob(mariadb_client)

    # When the job runs today, and again tomorrow morning
    job.refresh(NOW)
    tomorrow = TODAY + timedelta(days=1)
    _seed_polls(mariadb_client, _at(tomorrow, timedelta(hours=8)), room=18.0)
    job.refresh(datetime(2026, 11, 13, 9, 0, tzinfo=UTC))

    # Then today was raised at the boost, and tomorrow starts at 17.5
    days = _comfort_days(mariadb_client)
    assert days[TODAY].comfort_temp == 17.0
    assert days[TODAY].raised_at == _at(TODAY, HELD)
    assert days[TODAY].day_kind == "held_boost"
    assert days[TODAY].quiet_count == 0
    assert days[tomorrow].comfort_temp == 17.5


def test_a_second_held_boost_the_same_day_does_not_raise_again(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a Held Boost at 19:00 against 17.0, and another at 21:00 once Hive
    # holds the raised 17.5
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    _seed_held_boost(mariadb_client, TODAY)
    _seed_held_boost(
        mariadb_client, TODAY, comfort=17.5, room=17.2, at=timedelta(hours=21)
    )
    job = ComfortDayJob(mariadb_client)

    # When the job runs today, and again tomorrow morning
    job.refresh(datetime(2026, 11, 12, 22, 0, tzinfo=UTC))
    tomorrow = TODAY + timedelta(days=1)
    _seed_polls(mariadb_client, _at(tomorrow, timedelta(hours=8)), room=18.0)
    job.refresh(datetime(2026, 11, 13, 9, 0, tzinfo=UTC))

    # Then today was raised once, at the first, and tomorrow starts at 17.5
    days = _comfort_days(mariadb_client)
    assert days[TODAY].raised_at == _at(TODAY, HELD)
    assert days[tomorrow].comfort_temp == 17.5


def test_a_boost_with_the_room_more_than_half_a_degree_below_is_not_held(
    mariadb_client: MariaDBClient,
) -> None:
    # Given two Quiet Days counted, and today a boost at a room of 16.4 against
    # Hive holding 17.0 (a late warm-up)
    _seed_stepped_comfort(
        mariadb_client, TODAY - timedelta(days=1), 17.0, quiet_count=2
    )
    _seed_held_boost(mariadb_client, TODAY, room=16.4)

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then it is not held, and the count is unchanged
    today = _comfort_days(mariadb_client)[TODAY]
    assert today.raised_at is None
    assert today.day_kind is None
    assert today.quiet_count == 2


def test_a_boost_outside_the_comfort_window_is_not_held(
    mariadb_client: MariaDBClient,
) -> None:
    # Given Hive holding 17.0 at 06:00 during a pre-heat, before the 07:30
    # window opens, and a boost then at a room of 16.8
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    _seed_held_boost(mariadb_client, TODAY, room=16.8, at=timedelta(hours=6))

    ComfortDayJob(mariadb_client).refresh(NOW)

    assert _comfort_days(mariadb_client)[TODAY].raised_at is None


def test_a_boost_while_hive_holds_another_target_is_not_held(
    mariadb_client: MariaDBClient,
) -> None:
    # Given today's Comfort Temperature is 17.0 but Hive holds 10.0 (the Away
    # Override, a dry run or a manual change), and a boost at a room of 16.8
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    _seed_polls(
        mariadb_client,
        _at(TODAY, HELD - timedelta(minutes=10)),
        room=16.9,
        count=5,
        target=10.0,
    )
    _seed_polls(mariadb_client, _at(TODAY, HELD), room=16.8, count=3, boost=True)

    ComfortDayJob(mariadb_client).refresh(NOW)

    assert _comfort_days(mariadb_client)[TODAY].raised_at is None


def test_there_is_no_ceiling_on_a_raise(mariadb_client: MariaDBClient) -> None:
    # Given today holds 20.0 and a Held Boost at a room of 19.8
    _seed_stepped_comfort(mariadb_client, TODAY - timedelta(days=1), 20.0)
    _seed_held_boost(mariadb_client, TODAY, comfort=20.0, room=19.8)
    job = ComfortDayJob(mariadb_client)

    # When the job runs today, and again tomorrow morning
    job.refresh(NOW)
    tomorrow = TODAY + timedelta(days=1)
    _seed_polls(mariadb_client, _at(tomorrow, timedelta(hours=8)), room=20.0)
    job.refresh(datetime(2026, 11, 13, 9, 0, tzinfo=UTC))

    # Then tomorrow starts at 20.5
    assert _comfort_days(mariadb_client)[tomorrow].comfort_temp == 20.5


def test_minutes_below_follow_the_value_in_force_after_a_raise(
    mariadb_client: MariaDBClient,
) -> None:
    # Given the 18:00 and 19:00 hours are counted, Hive holds 17.0 with the room at
    # 17.0 from 18:50, a Held Boost at 19:00 at a room of 16.7 for three polls,
    # then the room at 16.9 for five polls from 19:10, 17.2 for five from 19:20
    # and exactly 17.0 at 19:30
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    last_week = TODAY - timedelta(days=7)
    _seed_heating_hour(mariadb_client, _at(last_week))
    _seed_heating_hour(mariadb_client, _at(last_week, HELD))
    _seed_held_boost(mariadb_client, TODAY, room=16.7)
    _seed_polls(
        mariadb_client, _at(TODAY, HELD + timedelta(minutes=10)), room=16.9, count=5
    )
    _seed_polls(
        mariadb_client, _at(TODAY, HELD + timedelta(minutes=20)), room=17.2, count=5
    )
    _seed_polls(mariadb_client, _at(TODAY, HELD + timedelta(minutes=30)), room=17.0)

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then nothing before 19:00 is below 16.5; from the raise to 17.5 the threshold
    # is 17.0, so the three boost polls and the five at 16.9 count, the five at
    # 17.2 and the one at exactly 17.0 do not: 16 minutes
    assert _comfort_days(mariadb_client)[TODAY].minutes_below == 16


def test_boost_starts_count_the_days_own_boosts(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a boost running across midnight into today, and today a boost of six
    # polls from 18:00, a boost poll 10 minutes after its last (the same boost),
    # one 12 minutes after that (a new one) and another at 20:00
    _seed_polls(
        mariadb_client,
        _at(TODAY, -timedelta(minutes=4)),
        room=16.0,
        count=4,
        boost=True,
    )
    _seed_polls(mariadb_client, _at(TODAY), room=16.0, count=6, boost=True)
    _seed_polls(
        mariadb_client,
        _at(TODAY, timedelta(hours=18, minutes=20)),
        room=17.0,
        boost=True,
    )
    _seed_polls(
        mariadb_client,
        _at(TODAY, timedelta(hours=18, minutes=32)),
        room=17.0,
        boost=True,
    )
    _seed_polls(mariadb_client, _at(TODAY, timedelta(hours=20)), room=17.0, boost=True)

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then today has three Boost Starts
    assert _comfort_days(mariadb_client)[TODAY].boost_starts == 3


def _seed_quiet_day(
    client: MariaDBClient,
    day: date,
    *,
    comfort: float = 17.0,
    minutes: int = 120,
    on: bool = True,
) -> None:
    """Hive holding `comfort` from 19:00 for `minutes` with the room at it, the
    heating on throughout when `on`, and no boost."""
    _seed_polls(
        client,
        _at(day, HELD),
        room=comfort,
        count=minutes // 2,
        target=comfort,
        on=on,
    )


def test_three_quiet_days_step_the_next_day_down_half_a_degree(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 17.0 with no Quiet Days counted four days ago, then three days each
    # with two hours held at home in the window, the heating on and no boost
    _seed_stepped_comfort(mariadb_client, TODAY - timedelta(days=4), 17.0)
    quiet_days = [TODAY - timedelta(days=n) for n in (3, 2, 1)]
    for day in quiet_days:
        _seed_quiet_day(mariadb_client, day)
    _seed_polls(mariadb_client, _at(TODAY, timedelta(hours=8)), room=17.0)

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then each is quiet, counting 1, 2, 3, and today starts 0.5 lower at 0
    days = _comfort_days(mariadb_client)
    assert [days[day].day_kind for day in quiet_days] == ["quiet"] * 3
    assert [days[day].quiet_count for day in quiet_days] == [1, 2, 3]
    assert days[TODAY].comfort_temp == 16.5
    assert days[TODAY].quiet_count == 0


def test_a_day_with_only_an_hour_held_at_home_is_not_quiet(
    mariadb_client: MariaDBClient,
) -> None:
    # Given two Quiet Days counted, and today only one hour held at home
    _seed_stepped_comfort(
        mariadb_client, TODAY - timedelta(days=1), 17.0, quiet_count=2
    )
    _seed_quiet_day(mariadb_client, TODAY, minutes=60)

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then it is neither quiet nor a raise, and the count is unchanged
    today = _comfort_days(mariadb_client)[TODAY]
    assert today.day_kind is None
    assert today.quiet_count == 2


def test_held_time_while_no_one_is_home_does_not_count_towards_a_quiet_day(
    mariadb_client: MariaDBClient,
) -> None:
    # Given two hours held from 19:00, but no one home from 20:00 to 21:00
    _seed_stepped_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    _seed_quiet_day(mariadb_client, TODAY)
    _seed_presence_period(
        mariadb_client,
        "no_one_home",
        _at(TODAY, timedelta(hours=20)),
        _at(TODAY, timedelta(hours=21)),
    )

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then only an hour was held at home: not quiet
    today = _comfort_days(mariadb_client)[TODAY]
    assert today.day_kind is None
    assert today.quiet_count == 0


def test_a_day_with_the_heating_never_on_in_the_window_is_not_quiet(
    mariadb_client: MariaDBClient,
) -> None:
    # Given two hours held at home, with the room warm enough that the heating
    # never came on
    _seed_stepped_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    _seed_quiet_day(mariadb_client, TODAY, on=False)

    ComfortDayJob(mariadb_client).refresh(NOW)

    assert _comfort_days(mariadb_client)[TODAY].day_kind is None


def test_a_day_with_no_evidence_neither_adds_to_nor_resets_the_count(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 17.0 five days ago, then a Quiet Day, a Quiet Day, a day away (a poll
    # but nothing held), and a Quiet Day
    _seed_stepped_comfort(mariadb_client, TODAY - timedelta(days=5), 17.0)
    _seed_quiet_day(mariadb_client, TODAY - timedelta(days=4))
    _seed_quiet_day(mariadb_client, TODAY - timedelta(days=3))
    _seed_polls(mariadb_client, _at(TODAY - timedelta(days=2)), room=12.0)
    _seed_quiet_day(mariadb_client, TODAY - timedelta(days=1))
    _seed_polls(mariadb_client, _at(TODAY, timedelta(hours=8)), room=17.0)

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then the counts run 1, 2, 2, 3, and the step comes after the fourth day
    days = _comfort_days(mariadb_client)
    assert [days[TODAY - timedelta(days=n)].quiet_count for n in (4, 3, 2, 1)] == [
        1,
        2,
        2,
        3,
    ]
    assert days[TODAY - timedelta(days=1)].comfort_temp == 17.0
    assert days[TODAY].comfort_temp == 16.5


def test_at_the_floor_three_quiet_days_leave_it_at_sixteen(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 16.0 with two Quiet Days counted, and a third Quiet Day at 16.0
    _seed_stepped_comfort(
        mariadb_client, TODAY - timedelta(days=2), 16.0, quiet_count=2
    )
    _seed_quiet_day(mariadb_client, TODAY - timedelta(days=1), comfort=16.0)
    _seed_polls(mariadb_client, _at(TODAY, timedelta(hours=8)), room=16.0)

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then today stays at 16.0 with the count reset
    days = _comfort_days(mariadb_client)
    assert days[TODAY - timedelta(days=1)].quiet_count == 3
    assert days[TODAY].comfort_temp == 16.0
    assert days[TODAY].quiet_count == 0


def test_an_old_value_below_the_floor_starts_the_next_day_at_sixteen(
    mariadb_client: MariaDBClient,
) -> None:
    # Given yesterday's row was settled by the old rule at 15.2
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 15.2)
    _seed_polls(mariadb_client, _at(TODAY), room=18.0)

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then today starts at the 16 C floor, not 15.5
    assert _comfort_days(mariadb_client)[TODAY].comfort_temp == 16.0


def test_polls_while_the_thermostat_is_offline_do_not_make_a_quiet_day(
    mariadb_client: MariaDBClient,
) -> None:
    # Given two hours in the window where Hive still reports 17.0 and the heating
    # on, but the thermostat was offline throughout (stale values)
    _seed_stepped_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    _seed_polls(
        mariadb_client,
        _at(TODAY, HELD),
        room=17.0,
        count=60,
        target=17.0,
        on=True,
        online=False,
    )

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then it is not quiet: an outage never moves the Comfort Temperature
    today = _comfort_days(mariadb_client)[TODAY]
    assert today.day_kind is None
    assert today.quiet_count == 0


def test_a_boost_after_an_offline_poll_is_not_held(
    mariadb_client: MariaDBClient,
) -> None:
    # Given Hive reports 17.0 with the thermostat offline, and then a boost at a
    # room of 16.8
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    _seed_polls(
        mariadb_client,
        _at(TODAY, HELD - timedelta(minutes=10)),
        room=17.0,
        count=5,
        target=17.0,
        online=False,
    )
    _seed_polls(mariadb_client, _at(TODAY, HELD), room=16.8, count=3, boost=True)

    ComfortDayJob(mariadb_client).refresh(NOW)

    assert _comfort_days(mariadb_client)[TODAY].raised_at is None


def test_a_boost_whose_first_reading_has_no_room_temperature_still_counts(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a boost whose first poll has no room reading, then readable polls
    _seed_polls(mariadb_client, _at(TODAY), room=None, boost=True)
    _seed_polls(
        mariadb_client,
        _at(TODAY, EVENING + timedelta(minutes=2)),
        room=16.5,
        count=2,
        boost=True,
    )

    ComfortDayJob(mariadb_client).refresh(NOW)

    assert _comfort_days(mariadb_client)[TODAY].boost_starts == 1


def test_heating_on_only_while_offline_does_not_make_a_quiet_day(
    mariadb_client: MariaDBClient,
) -> None:
    # Given two hours held at home online with the heating off, and the only
    # heating-on polls from while the thermostat was offline
    _seed_stepped_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    _seed_quiet_day(mariadb_client, TODAY, on=False)
    _seed_polls(
        mariadb_client,
        _at(TODAY, timedelta(hours=21)),
        room=17.0,
        count=5,
        target=17.0,
        on=True,
        online=False,
    )

    ComfortDayJob(mariadb_client).refresh(NOW)

    assert _comfort_days(mariadb_client)[TODAY].day_kind is None


def test_offline_readings_add_no_minutes_below(mariadb_client: MariaDBClient) -> None:
    # Given a cold evening (ten polls at 16.4 against 17.0 in a counted hour) read
    # while the thermostat was offline, so the reading is stale
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    _seed_heating_hour(mariadb_client, _at(TODAY - timedelta(days=7)))
    _seed_polls(mariadb_client, _at(TODAY), room=16.4, count=10, online=False)

    ComfortDayJob(mariadb_client).refresh(NOW)

    assert _comfort_days(mariadb_client)[TODAY].minutes_below == 0


def test_a_day_with_a_boost_that_was_not_held_is_not_quiet(
    mariadb_client: MariaDBClient,
) -> None:
    # Given two Quiet Days counted, and today a boost at 08:00 during a late
    # warm-up (room 15.8 against 17.0, not held), then two hours held at home
    _seed_stepped_comfort(
        mariadb_client, TODAY - timedelta(days=1), 17.0, quiet_count=2
    )
    _seed_held_boost(mariadb_client, TODAY, room=15.8, at=timedelta(hours=8))
    _seed_quiet_day(mariadb_client, TODAY)

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then the day counts for nothing: neither quiet nor a raise, count unchanged
    today = _comfort_days(mariadb_client)[TODAY]
    assert today.day_kind is None
    assert today.quiet_count == 2


def test_a_boost_with_the_room_at_the_comfort_temperature_is_held(
    mariadb_client: MariaDBClient,
) -> None:
    # Given Hive holding 17.0 and a boost with the room already at 17.0
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    _seed_held_boost(mariadb_client, TODAY, room=17.0)

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then it is held: 17.0 is too cold even when the room reads 17.0
    today = _comfort_days(mariadb_client)[TODAY]
    assert today.raised_at == _at(TODAY, HELD)
    assert today.day_kind == "held_boost"


def test_a_boost_with_the_room_above_the_comfort_temperature_is_held(
    mariadb_client: MariaDBClient,
) -> None:
    # Given Hive holding 17.0 and a boost with the room at 17.2
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    _seed_held_boost(mariadb_client, TODAY, room=17.2)

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then it is held: there is no upper limit
    assert _comfort_days(mariadb_client)[TODAY].raised_at == _at(TODAY, HELD)


def test_a_boost_just_below_the_comfort_temperature_is_held(
    mariadb_client: MariaDBClient,
) -> None:
    # Given Hive holding 17.0 and a boost with the room at 16.9
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    _seed_held_boost(mariadb_client, TODAY, room=16.9)

    ComfortDayJob(mariadb_client).refresh(NOW)

    assert _comfort_days(mariadb_client)[TODAY].raised_at == _at(TODAY, HELD)


def test_a_boost_exactly_half_a_degree_below_is_held(
    mariadb_client: MariaDBClient,
) -> None:
    # Given Hive holding 17.0 and a boost with the room at 16.5
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    _seed_held_boost(mariadb_client, TODAY, room=16.5)

    ComfortDayJob(mariadb_client).refresh(NOW)

    assert _comfort_days(mariadb_client)[TODAY].raised_at == _at(TODAY, HELD)


def test_a_reading_within_half_a_degree_adds_no_minutes_below(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 17.0, the 18:00 hour counted, and ten polls at 16.6 in it
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    _seed_heating_hour(mariadb_client, _at(TODAY - timedelta(days=7)))
    _seed_polls(mariadb_client, _at(TODAY), room=16.6, count=10)

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then the thermostat's normal swing is not discomfort
    assert _comfort_days(mariadb_client)[TODAY].minutes_below == 0


def test_a_reading_exactly_half_a_degree_below_adds_no_minutes_below(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 17.0, the 18:00 hour counted, and ten polls at exactly 16.5 in it
    _seed_stored_comfort(mariadb_client, TODAY - timedelta(days=1), 17.0)
    _seed_heating_hour(mariadb_client, _at(TODAY - timedelta(days=7)))
    _seed_polls(mariadb_client, _at(TODAY), room=16.5, count=10)

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then only readings more than half a degree below count
    assert _comfort_days(mariadb_client)[TODAY].minutes_below == 0


FRIDAY = TODAY + timedelta(days=1)  # its default window runs to 00:30 Saturday
SATURDAY = TODAY + timedelta(days=2)


def _saturday(hour: int, minute: int) -> datetime:
    return datetime(2026, 11, 14, hour, minute, tzinfo=UTC)


def test_a_held_boost_after_midnight_raises_the_evening_before(
    mariadb_client: MariaDBClient,
) -> None:
    # Given Thursday's stored 17.0, a poll on Friday, Hive holding 17.0 just after
    # midnight, a boost at 00:15 on Saturday with the room at 16.8, and a poll on
    # Saturday morning
    _seed_stepped_comfort(mariadb_client, TODAY, 17.0)
    _seed_polls(mariadb_client, _at(FRIDAY), room=17.0)
    _seed_held_boost(mariadb_client, SATURDAY, room=16.8, at=timedelta(minutes=15))
    _seed_polls(mariadb_client, _at(SATURDAY, timedelta(hours=8)), room=17.0)

    # When the job runs after 00:30
    ComfortDayJob(mariadb_client).refresh(_saturday(1, 5))

    # Then Friday is raised at the boost, and Saturday starts at 17.5
    days = _comfort_days(mariadb_client)
    assert days[FRIDAY].raised_at == _at(SATURDAY, timedelta(minutes=15))
    assert days[FRIDAY].day_kind == "held_boost"
    assert days[SATURDAY].comfort_temp == 17.5


def test_the_evening_before_settles_only_after_its_window_closes(
    mariadb_client: MariaDBClient,
) -> None:
    # Given Friday's row was written at 00:04 on Saturday, before its window
    # closed at 00:30
    _seed_stepped_comfort(mariadb_client, TODAY, 17.0)
    _seed_polls(mariadb_client, _at(FRIDAY), room=17.0)
    job = ComfortDayJob(mariadb_client)
    job.refresh(_saturday(0, 4))

    # When a Held Boost at 00:15 turns up and the run after 00:30 happens, and then
    # another run an hour later
    _seed_held_boost(mariadb_client, SATURDAY, room=16.8, at=timedelta(minutes=15))
    job.refresh(_saturday(1, 5))
    job.refresh(_saturday(2, 5))

    # Then Friday was rewritten and settled by the 01:05 run, not after it
    friday = _comfort_days(mariadb_client)[FRIDAY]
    assert friday.raised_at == _at(SATURDAY, timedelta(minutes=15))
    assert friday.computed_at == datetime(2026, 11, 14, 1, 5)


def test_held_time_after_midnight_counts_towards_the_evenings_quiet_day(
    mariadb_client: MariaDBClient,
) -> None:
    # Given Friday held at home with the heating on for 90 minutes before midnight
    # (22:30 to 23:58) and 30 minutes after it (00:00 to 00:28), and no boost
    _seed_stepped_comfort(mariadb_client, TODAY, 17.0)
    _seed_polls(
        mariadb_client,
        _at(FRIDAY, timedelta(hours=22, minutes=30)),
        room=17.0,
        count=45,
        target=17.0,
        on=True,
    )
    _seed_polls(
        mariadb_client,
        _at(SATURDAY, timedelta(0)),
        room=17.0,
        count=15,
        target=17.0,
        on=True,
    )

    # When the job runs after 00:30
    ComfortDayJob(mariadb_client).refresh(_saturday(1, 5))

    # Then Friday is a Quiet Day: two hours held across midnight
    friday = _comfort_days(mariadb_client)[FRIDAY]
    assert friday.day_kind == "quiet"
    assert friday.quiet_count == 1


def test_minutes_below_after_midnight_still_count_for_the_calendar_day(
    mariadb_client: MariaDBClient,
) -> None:
    # Given the 00:00 hour is counted, and polls at 16.0 from 00:00 to 00:18 on
    # Saturday, inside Friday's window
    _seed_stepped_comfort(mariadb_client, TODAY, 17.0)
    _seed_heating_hour(mariadb_client, _at(TODAY - timedelta(days=7), timedelta(0)))
    _seed_polls(mariadb_client, _at(FRIDAY), room=17.0)
    _seed_polls(mariadb_client, _at(SATURDAY, timedelta(0)), room=16.0, count=10)

    # When the job runs after 00:30
    ComfortDayJob(mariadb_client).refresh(_saturday(1, 5))

    # Then they are Saturday's minutes below, not Friday's
    days = _comfort_days(mariadb_client)
    assert days[SATURDAY].minutes_below == 20
    assert days[FRIDAY].minutes_below == 0


def test_leaving_after_midnight_stops_the_tail_counting_as_held_at_home(
    mariadb_client: MariaDBClient,
) -> None:
    # Given Friday held with the heating on from 22:30 to 23:58 and from 00:00 to
    # 00:28, but no one home from 00:10 on Saturday
    _seed_stepped_comfort(mariadb_client, TODAY, 17.0)
    _seed_polls(
        mariadb_client,
        _at(FRIDAY, timedelta(hours=22, minutes=30)),
        room=17.0,
        count=45,
        target=17.0,
        on=True,
    )
    _seed_polls(
        mariadb_client,
        _at(SATURDAY, timedelta(0)),
        room=17.0,
        count=15,
        target=17.0,
        on=True,
    )
    _seed_presence_period(
        mariadb_client, "no_one_home", _at(SATURDAY, timedelta(minutes=10))
    )

    # When the job runs after 00:30
    ComfortDayJob(mariadb_client).refresh(_saturday(1, 5))

    # Then only 100 minutes were held at home: Friday is not quiet
    assert _comfort_days(mariadb_client)[FRIDAY].day_kind is None


def test_a_boost_after_midnight_counts_in_the_calendar_days_boost_starts(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a boost at 00:15 on Saturday, inside Friday's window
    _seed_stepped_comfort(mariadb_client, TODAY, 17.0)
    _seed_polls(mariadb_client, _at(FRIDAY), room=17.0)
    _seed_polls(
        mariadb_client,
        _at(SATURDAY, timedelta(minutes=15)),
        room=16.0,
        count=3,
        boost=True,
    )

    ComfortDayJob(mariadb_client).refresh(_saturday(1, 5))

    # Then it is one of Saturday's Boost Starts, not Friday's
    days = _comfort_days(mariadb_client)
    assert days[SATURDAY].boost_starts == 1
    assert days[FRIDAY].boost_starts == 0


def test_an_unsettled_day_with_no_polls_still_leads_the_next_day(
    mariadb_client: MariaDBClient,
) -> None:
    # Given Thursday stored at 17.0, Friday stored at 17.5 but written at 00:04 on
    # Saturday (unsettled, its window runs to 00:30) with no polls to rewrite it
    # from, and a poll on Saturday
    _seed_stepped_comfort(mariadb_client, TODAY, 17.0)
    mariadb_client.write_comfort_days(
        [
            ComfortDayRecord(
                date=FRIDAY,
                comfort_temp=17.5,
                boost_starts=0,
                counted_hours="",
                minutes_below=0,
                computed_at=datetime(2026, 11, 14, 0, 4),
                quiet_count=0,
            )
        ]
    )
    _seed_polls(mariadb_client, _at(SATURDAY, timedelta(hours=8)), room=17.0)

    # When the job runs after 00:30
    ComfortDayJob(mariadb_client).refresh(_saturday(1, 5))

    # Then Saturday starts from Friday's stored 17.5, not Thursday's 17.0
    assert _comfort_days(mariadb_client)[SATURDAY].comfort_temp == 17.5


def test_readings_in_yesterdays_tail_compare_with_yesterdays_value_in_force(
    mariadb_client: MariaDBClient,
) -> None:
    # Given the 00:00 hour is counted, Friday at 17.0, a reading of 16.8 at 00:01 on
    # Saturday, then Hive holding 17.0 and a Held Boost at 00:15 with the room at
    # 16.8 for three polls (Friday rises to 17.5 from 00:15)
    _seed_stepped_comfort(mariadb_client, TODAY, 17.0)
    _seed_heating_hour(mariadb_client, _at(TODAY - timedelta(days=7), timedelta(0)))
    _seed_polls(mariadb_client, _at(FRIDAY), room=17.0)
    _seed_polls(
        mariadb_client, _at(SATURDAY, timedelta(minutes=1)), room=16.8, target=17.0
    )
    _seed_held_boost(mariadb_client, SATURDAY, room=16.8, at=timedelta(minutes=15))

    # When the job runs after 00:30
    ComfortDayJob(mariadb_client).refresh(_saturday(1, 5))

    # Then Saturday starts at 17.5, but the 00:01 reading is compared with Friday's
    # 17.0 then in force (not below 16.5), and only the three boost polls after
    # the raise are below 17.0: 6 minutes
    saturday = _comfort_days(mariadb_client)[SATURDAY]
    assert saturday.comfort_temp == 17.5
    assert saturday.minutes_below == 6
