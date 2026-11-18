from datetime import UTC, date, datetime, timedelta

from octopus_app.data.comfort_job import ComfortDayJob
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


def test_a_boost_from_last_season_is_used_and_one_over_two_years_old_is_not(
    mariadb_client: MariaDBClient,
) -> None:
    # Given four recent boosts, one 91 days ago, and a cold one 731 days ago
    _seed_boosts_on_earlier_days(mariadb_client, [16.0, 16.5, 17.5, 20.0])
    _seed_polls(mariadb_client, _at(TODAY - timedelta(days=91)), room=17.0, boost=True)
    _seed_polls(mariadb_client, _at(TODAY - timedelta(days=731)), room=5.0, boost=True)
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
    # have since left the two-year window
    learned_on = TODAY - timedelta(days=5)
    _seed_boosts_on_earlier_days(
        mariadb_client,
        [16.0, 16.5, 17.0, 17.5, 20.0],
        before=TODAY - timedelta(days=730),
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


def test_an_hour_heated_last_season_is_counted_and_one_over_a_year_ago_is_not(
    mariadb_client: MariaDBClient,
) -> None:
    # Given heating in the 18:00 hour 91 days ago and the 06:00 hour 366 days ago
    _seed_boosts_on_earlier_days(mariadb_client, [16.0, 16.5, 17.0, 17.5, 20.0])
    # (91 days ago was in British Summer Time: 17:00 UTC is 18:00 in London)
    _seed_heating_hour(
        mariadb_client, _at(TODAY - timedelta(days=91), timedelta(hours=17))
    )
    _seed_heating_hour(
        mariadb_client, _at(TODAY - timedelta(days=366), timedelta(hours=6))
    )
    _seed_polls(mariadb_client, _at(TODAY), room=16.5, count=10)

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
    _seed_boosts_on_earlier_days(
        mariadb_client, [16.0, 16.5, 17.0, 17.5, 20.0], before=summer_day
    )
    _seed_heating_hour(mariadb_client, _at(summer_day - timedelta(days=7), five_pm_utc))
    _seed_polls(mariadb_client, _at(summer_day, five_pm_utc), room=16.5, count=10)

    ComfortDayJob(mariadb_client).refresh(datetime(2026, 7, 15, 20, 0, tzinfo=UTC))

    today = _comfort_days(mariadb_client)[summer_day]
    assert today.counted_hours == "18"
    assert today.minutes_below == 20


def test_a_boost_running_across_the_start_of_the_window_is_not_a_new_boost(
    mariadb_client: MariaDBClient,
) -> None:
    # Given four boosts in the window, and one that started two minutes before
    # the window opened and was still running as it did
    _seed_boosts_on_earlier_days(mariadb_client, [16.0, 16.5, 17.0, 17.5])
    window_opens = _at(TODAY - timedelta(days=730), timedelta(0))
    _seed_polls(
        mariadb_client,
        window_opens - timedelta(minutes=2),
        room=10.0,
        count=2,
        boost=True,
    )
    _seed_polls(mariadb_client, _at(TODAY), room=18.0)
    job = ComfortDayJob(mariadb_client)
    job.refresh(NOW)

    # When a later run reads only today's window
    job.refresh(NOW + timedelta(hours=1))

    today = _comfort_days(mariadb_client)[TODAY]
    assert today.boost_starts == 4
    assert today.comfort_temp is None


def test_a_settled_day_stays_settled_while_no_later_day_has_polls(
    mariadb_client: MariaDBClient,
) -> None:
    # Given yesterday was settled after midnight, and Hive has sent nothing today
    yesterday = TODAY - timedelta(days=1)
    _seed_polls(mariadb_client, _at(yesterday), room=18.0)
    job = ComfortDayJob(mariadb_client)
    settled_at = datetime(2026, 11, 12, 0, 30, tzinfo=UTC)
    job.refresh(settled_at)

    # When evidence from before yesterday turns up and the job runs again
    _seed_boosts_on_earlier_days(
        mariadb_client, [16.0, 16.5, 17.0, 17.5, 20.0], before=yesterday
    )
    job.refresh(NOW)

    row = _comfort_days(mariadb_client)[yesterday]
    assert row.computed_at == settled_at.replace(tzinfo=None)
    assert row.comfort_temp is None


def _seed_old_and_new_boosts(client: MariaDBClient) -> None:
    """Four boosts at 16.0 C on the days 100 to 97 days ago, and two at 19.0 C
    on the last two days."""
    _seed_boosts_on_earlier_days(
        client, [16.0, 16.0, 16.0, 16.0], before=TODAY - timedelta(days=96)
    )
    _seed_boosts_on_earlier_days(client, [19.0, 19.0])
    _seed_polls(client, _at(TODAY), room=18.0)


def test_a_boost_sixty_heating_days_old_counts_half_as_much_as_a_new_one(
    mariadb_client: MariaDBClient,
) -> None:
    # Given four old boosts at 16.0 C, then 60 days with the heating on, then two
    # new boosts at 19.0 C
    _seed_old_and_new_boosts(mariadb_client)
    for days_ago in range(90, 30, -1):
        _seed_heating_hour(mariadb_client, _at(TODAY - timedelta(days=days_ago)))

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then four at half weight balance two at full weight: the midpoint
    assert _comfort_days(mariadb_client)[TODAY].comfort_temp == 17.5


def test_days_without_heating_do_not_age_a_boost(
    mariadb_client: MariaDBClient,
) -> None:
    # Given the same boosts with no heating at all in between (a summer)
    _seed_old_and_new_boosts(mariadb_client)

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then all six weigh the same, and the plain median is 16.0
    assert _comfort_days(mariadb_client)[TODAY].comfort_temp == 16.0


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
    polls at 16.5 C from 18:00 to 18:18."""
    _seed_boosts_on_earlier_days(client, [16.0, 16.5, 17.0, 17.5, 20.0], before=day)
    _seed_heating_hour(client, _at(day - timedelta(days=7)))
    _seed_polls(client, _at(day), room=16.5, count=10)


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

    # Then the polls at 18:06, 18:08 and 18:10 add nothing: 7 polls, 14 minutes
    assert _comfort_days(mariadb_client)[TODAY].minutes_below == 14


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


def test_a_boost_started_while_no_one_is_home_still_counts_as_evidence(
    mariadb_client: MariaDBClient,
) -> None:
    # Given five boosts on earlier days, all while no one was home
    _seed_presence_period(
        mariadb_client, "no_one_home", _at(TODAY - timedelta(days=6)), _at(TODAY)
    )
    _seed_boosts_on_earlier_days(mariadb_client, [16.0, 16.5, 17.0, 17.5, 20.0])
    _seed_polls(mariadb_client, _at(TODAY), room=18.0)

    ComfortDayJob(mariadb_client).refresh(NOW)

    # Then their Boost Starts still set today's comfort temperature
    today = _comfort_days(mariadb_client)[TODAY]
    assert today.boost_starts == 5
    assert today.comfort_temp == 17.0
