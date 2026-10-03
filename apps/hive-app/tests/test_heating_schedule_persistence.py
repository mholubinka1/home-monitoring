"""The heating schedule as apyhiveapi really returns it contains datetimes; the
`schedule` JSON column can only store JSON, so the write must make it JSON-safe.

The shape below is copied from apyhiveapi's HiveHelper.getScheduleNNL: each of
now/next/later is a Hive API slot dict (`value`, `start`) with `Start_DateTime`
and `End_DateTime` datetimes added by the library."""

# apyhiveapi builds these datetimes naive, so the fixtures are deliberately naive.
# ruff: noqa: DTZ001

from datetime import UTC, date, datetime
from typing import Any

from hive_app.data.model import HeatingStatus
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient


def _real_shaped_schedule() -> dict[str, Any]:
    return {
        "now": {
            "value": {"target": 7},
            "start": 720,
            "Start_DateTime": datetime(2026, 10, 3, 12, 0),
            "End_DateTime": datetime(2026, 10, 3, 17, 30),
        },
        "next": {
            "value": {"target": 21},
            "start": 1050,
            "Start_DateTime": datetime(2026, 10, 3, 17, 30),
            "End_DateTime": datetime(2026, 10, 3, 22, 0),
        },
        "later": {
            "value": {"target": 7},
            "start": 1320,
            "Start_DateTime": datetime(2026, 10, 3, 22, 0),
            "End_DateTime": datetime(2026, 10, 4, 6, 30),
        },
    }


def _status(schedule: dict[str, Any]) -> HeatingStatus:
    return HeatingStatus(
        polled_at=datetime(2026, 10, 3, 13, 49, tzinfo=UTC),
        current_temp=18.5,
        target_temp=7.0,
        mode="SCHEDULE",
        state="OFF",
        boost_active=False,
        boost_ends_at=None,
        schedule=schedule,
    )


def test_a_poll_whose_schedule_holds_datetimes_is_stored_with_iso_timestamps(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_heating_status(_status(_real_shaped_schedule()))

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.heating_status).one()

    assert stored.schedule["now"] == {
        "value": {"target": 7},
        "start": 720,
        "Start_DateTime": "2026-10-03T12:00:00",
        "End_DateTime": "2026-10-03T17:30:00",
    }
    assert stored.schedule["next"]["Start_DateTime"] == "2026-10-03T17:30:00"
    assert stored.schedule["later"]["End_DateTime"] == "2026-10-04T06:30:00"


def test_a_schedule_without_datetimes_is_stored_unchanged(
    mariadb_client: MariaDBClient,
) -> None:
    string_only = {"now": "21.0", "next": "18.0", "later": "19.0"}
    nested_ints = {"now": {"value": {"target": 7}, "start": 720, "active": True}}

    mariadb_client.write_heating_status(_status(string_only))
    mariadb_client.write_heating_status(_status(nested_ints))

    with mariadb_client.session_read_scope() as session:
        stored = (
            session.query(model.heating_status).order_by(model.heating_status.id).all()
        )

    assert [row.schedule for row in stored] == [string_only, nested_ints]


def test_polled_at_and_boost_ends_at_stay_datetimes_when_the_schedule_is_converted(
    mariadb_client: MariaDBClient,
) -> None:
    boost_ends_at = datetime(2026, 10, 3, 14, 30, tzinfo=UTC)
    status = _status(_real_shaped_schedule())
    status.boost_ends_at = boost_ends_at

    mariadb_client.write_heating_status(status)

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.heating_status).one()

    assert isinstance(stored.polled_at, datetime)
    assert isinstance(stored.boost_ends_at, datetime)
    # SQLite stores naive datetimes, so compare without tzinfo.
    assert stored.polled_at == status.polled_at.replace(tzinfo=None)
    assert stored.boost_ends_at == boost_ends_at.replace(tzinfo=None)


def test_writing_a_status_does_not_alter_the_callers_schedule(
    mariadb_client: MariaDBClient,
) -> None:
    status = _status(_real_shaped_schedule())

    mariadb_client.write_heating_status(status)

    assert status.schedule == _real_shaped_schedule()
    assert status.schedule["now"]["Start_DateTime"] == datetime(2026, 10, 3, 12, 0)


def test_datetimes_nested_in_lists_and_plain_dates_are_stored_as_iso_strings(
    mariadb_client: MariaDBClient,
) -> None:
    schedule = {
        "slots": [{"at": datetime(2026, 10, 3, 12, 0)}, {"at": datetime(2026, 10, 4)}],
        "day": date(2026, 10, 3),
    }

    mariadb_client.write_heating_status(_status(schedule))

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.heating_status).one()

    assert stored.schedule == {
        "slots": [{"at": "2026-10-03T12:00:00"}, {"at": "2026-10-04T00:00:00"}],
        "day": "2026-10-03",
    }
