"""The Comfort Schedule: HeatingRetriever writes the Comfort Plan into Hive's weekly
schedule (spec: feature-presence-autonomous-heating, issue #808).

Driven through HeatingRetriever.refresh(now) with a fake HiveSource that records the
schedule writes it is asked to make, and the real MariaDB test client underneath.
The plan slots belong to octopus-app, so they are seeded here with plain SQL.

London is on GMT in November, so London clock time is UTC. The made-up slot is
05:40 to 06:25 at 19 C."""

import copy
import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from hive_app.common.config import HiveApplicationSettings
from hive_app.common.logging import APP_LOGGER_NAME
from hive_app.data.comfort_schedule import WeekRuleError
from hive_app.data.heating import HeatingRetriever
from hive_app.data.model import HeatingStatus, HiveAuthState
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.presence import NO_ONE_HOME, SOMEONE_HOME
from hive_app.main import build_heating_retriever

NOW = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)  # a Wednesday
TODAY = date(2026, 11, 18)
TOMORROW = date(2026, 11, 19)
AWAY = 7.0
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class _FakeHiveSource:
    """A fake HiveSource: a settable HeatingStatus and a recorded list of the
    weekly schedules it was asked to write."""

    def __init__(self, mariadb: MariaDBClient, status: HeatingStatus) -> None:
        self._mariadb = mariadb
        self.status = status
        self.schedule_writes: list[dict[str, Any]] = []
        # As the real HiveApiSource: a posted week is "sent", "offline" when
        # the write's own session found the product offline.
        self.schedule_outcome = "sent"
        self.schedule_error: Exception | None = None
        # Every write verb called, in order.
        self.calls: list[str] = []

    def fetch_heating_status(self) -> HeatingStatus:
        return self.status

    def persist_heating_status(self, status: HeatingStatus) -> None:
        self._mariadb.write_heating_status(status)

    def write_comfort_schedule(self, week: dict[str, Any]) -> str:
        if self.schedule_error is not None:
            raise self.schedule_error
        self.calls.append("comfort_schedule")
        self.schedule_writes.append(copy.deepcopy(week))
        return self.schedule_outcome

    def apply_away_override(self, _temperature: float) -> str:
        self.calls.append("away_override")
        return "applied"

    def restore_schedule(self) -> str:
        self.calls.append("restore_schedule")
        return "applied"

    def read_auth_state(self) -> HiveAuthState | None:
        raise NotImplementedError

    def login(self) -> HiveAuthState:
        raise NotImplementedError

    def resume(self, state: HiveAuthState) -> HiveAuthState:
        raise NotImplementedError

    def persist_auth_state(self, state: HiveAuthState) -> None:
        raise NotImplementedError


def _hives_week() -> dict[str, Any]:
    """The week Hive holds: 06:30 to 07:15 at 18 C, 7 C otherwise, every day. Like
    Hive's own week, no day has an entry at 00:00: the night carries on at the
    previous day's last setting."""
    return {
        day: [
            {"start": 390, "value": {"target": 18}},
            {"start": 435, "value": {"target": 7}},
        ]
        for day in DAYS
    }


def _status(weekly_schedule: dict[str, Any] | None) -> HeatingStatus:
    return HeatingStatus(
        polled_at=NOW,
        current_temp=18.0,
        target_temp=19.0,
        mode="SCHEDULE",
        state="OFF",
        boost_active=False,
        boost_ends_at=None,
        schedule={},
        weekly_schedule=weekly_schedule,
    )


def _only(items: list[Any]) -> Any:
    """The one item the list must hold."""
    assert len(items) == 1
    return items[0]


def _actions(mariadb: MariaDBClient) -> list[Any]:
    with mariadb.session_read_scope() as session:
        rows = session.query(model.heating_control_action).order_by(
            model.heating_control_action.id
        )
        session.expunge_all()
        return list(rows)


def _seed_plan_slots(
    mariadb: MariaDBClient, rows: list[tuple[date, str, int, datetime, datetime, float]]
) -> None:
    """room_forecast_slot rows: (day, scenario, position, starts_at, ends_at,
    target), naive UTC instants as octopus-app stores them."""
    with mariadb.session_write_scope() as session:
        session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS room_forecast_slot ("
                "day DATE, scenario TEXT, position INTEGER, starts_at DATETIME, "
                "ends_at DATETIME, target FLOAT, computed_at DATETIME, "
                "PRIMARY KEY (day, scenario, position))"
            )
        )
        for day, scenario, position, starts_at, ends_at, target in rows:
            session.execute(
                text(
                    "INSERT INTO room_forecast_slot VALUES "
                    "(:day, :scenario, :position, :starts_at, :ends_at, :target, "
                    ":computed_at)"
                ),
                {
                    "day": day,
                    "scenario": scenario,
                    "position": position,
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "target": target,
                    "computed_at": starts_at,
                },
            )


def test_the_plan_slots_of_the_asked_days_are_read_in_order(
    mariadb_client: MariaDBClient,
) -> None:
    # Given plan slots for today and tomorrow, a current-scenario slot (which is
    # not the plan) and a plan slot for a day not asked for
    _seed_plan_slots(
        mariadb_client,
        [
            (
                TODAY,
                "plan",
                1,
                datetime(2026, 11, 18, 17, 0),
                datetime(2026, 11, 19, 0, 0),
                19.0,
            ),
            (
                TODAY,
                "plan",
                0,
                datetime(2026, 11, 18, 5, 40),
                datetime(2026, 11, 18, 6, 25),
                19.0,
            ),
            (
                TODAY,
                "current",
                0,
                datetime(2026, 11, 18, 6, 0),
                datetime(2026, 11, 18, 7, 0),
                21.0,
            ),
            (
                TOMORROW,
                "plan",
                0,
                datetime(2026, 11, 19, 5, 40),
                datetime(2026, 11, 19, 6, 25),
                19.0,
            ),
            (
                date(2026, 11, 20),
                "plan",
                0,
                datetime(2026, 11, 20, 5, 40),
                datetime(2026, 11, 20, 6, 25),
                19.0,
            ),
        ],
    )

    # When the plan slots of today and tomorrow are read
    slots = mariadb_client.read_plan_slots([TODAY, TOMORROW])

    # Then only those days' plan slots come back, by day and position
    assert slots == [
        (TODAY, datetime(2026, 11, 18, 5, 40), datetime(2026, 11, 18, 6, 25), 19.0),
        (TODAY, datetime(2026, 11, 18, 17, 0), datetime(2026, 11, 19, 0, 0), 19.0),
        (TOMORROW, datetime(2026, 11, 19, 5, 40), datetime(2026, 11, 19, 6, 25), 19.0),
    ]


def test_a_plan_slot_replaces_its_day_and_the_whole_week_is_written(
    mariadb_client: MariaDBClient,
) -> None:
    # Given the Comfort Schedule is on, with no presence section, and today's
    # plan is 05:40 to 06:25 at 19 C, while Hive holds its own week
    _seed_plan_slots(
        mariadb_client,
        [
            (
                TODAY,
                "plan",
                0,
                datetime(2026, 11, 18, 5, 40),
                datetime(2026, 11, 18, 6, 25),
                19.0,
            )
        ],
    )
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))
    retriever = HeatingRetriever(
        source, mariadb_client, comfort_control="on", away_temperature=AWAY
    )

    # When the heating poll runs
    retriever.refresh(NOW)

    # Then all seven days are posted with today's replaced, as Hive writes its own
    # days: 19 C from 05:40 (minute 340) and the Away Temperature from 06:25
    # (minute 385), with no entry at 00:00; the other days are as Hive holds them
    expected = _hives_week()
    expected["wednesday"] = [
        {"start": 340, "value": {"target": 19.0}},
        {"start": 385, "value": {"target": AWAY}},
    ]
    assert source.schedule_writes == [expected]
    # and the write is recorded as a comfort_schedule action
    # and the write is recorded as a sent comfort_schedule action carrying the
    # week's fingerprint, for a later poll to confirm
    (action,) = _actions(mariadb_client)
    assert (action.action, action.outcome, action.dry_run) == (
        "comfort_schedule",
        "sent",
        False,
    )
    assert re.fullmatch(r"[0-9a-f]{64}", action.detail or "")


def _retriever(mariadb: MariaDBClient, source: _FakeHiveSource) -> HeatingRetriever:
    return HeatingRetriever(
        source, mariadb, comfort_control="on", away_temperature=AWAY
    )


def test_nothing_is_written_when_the_built_week_equals_hives(
    mariadb_client: MariaDBClient,
) -> None:
    # Given Hive already holds today's plan: 19 C from 05:40, Away from 06:25
    _seed_plan_slots(
        mariadb_client,
        [
            (
                TODAY,
                "plan",
                0,
                datetime(2026, 11, 18, 5, 40),
                datetime(2026, 11, 18, 6, 25),
                19.0,
            )
        ],
    )
    week = _hives_week()
    week["wednesday"] = [
        {"start": 340, "value": {"target": 19}},
        {"start": 385, "value": {"target": 7}},
    ]
    source = _FakeHiveSource(mariadb_client, _status(week))

    # When the heating poll runs
    _retriever(mariadb_client, source).refresh(NOW)

    # Then nothing is written and nothing is recorded
    assert not source.schedule_writes
    assert not _actions(mariadb_client)


def test_a_plan_slot_ending_at_midnight_closes_with_the_away_temperature_at_2359(
    mariadb_client: MariaDBClient,
) -> None:
    # Given today's plan slot runs from 17:00 to midnight at 19 C
    _seed_plan_slots(
        mariadb_client,
        [
            (
                TODAY,
                "plan",
                0,
                datetime(2026, 11, 18, 17, 0),
                datetime(2026, 11, 19, 0, 0),
                19.0,
            )
        ],
    )
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))

    # When the heating poll runs
    _retriever(mariadb_client, source).refresh(NOW)

    # Then Wednesday is 19 C from 17:00 (minute 1020) and the Away Temperature from
    # 23:59 (minute 1439), so 19 C never runs on into Thursday's night (Hive holds a
    # day's last setting until the next day's first entry)
    written = _only(source.schedule_writes)
    assert written["wednesday"] == [
        {"start": 1020, "value": {"target": 19.0}},
        {"start": 1439, "value": {"target": AWAY}},
    ]


def test_a_slot_starting_at_midnight_is_its_days_only_entry_at_minute_0(
    mariadb_client: MariaDBClient,
) -> None:
    # Given today's plan slot runs from 00:00 to 06:25 at 19 C
    _seed_plan_slots(
        mariadb_client,
        [
            (
                TODAY,
                "plan",
                0,
                datetime(2026, 11, 18, 0, 0),
                datetime(2026, 11, 18, 6, 25),
                19.0,
            )
        ],
    )
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))

    # When the heating poll runs
    _retriever(mariadb_client, source).refresh(NOW)

    # Then Wednesday's first entry is 19 C at minute 0, with no other entry at
    # minute 0, and the Away Temperature from 06:25 (minute 385)
    written = _only(source.schedule_writes)
    assert written["wednesday"] == [
        {"start": 0, "value": {"target": 19.0}},
        {"start": 385, "value": {"target": AWAY}},
    ]


def test_a_slot_that_does_not_lie_within_its_day_is_not_written_and_is_warned_of(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    # Given today's plan slot starts the evening before (23:30 on Tuesday) and runs
    # to 06:00, as a window opening just after midnight would plan, while
    # tomorrow's slot is the ordinary 05:40 to 06:25
    _seed_plan_slots(
        mariadb_client,
        [
            (
                TODAY,
                "plan",
                0,
                datetime(2026, 11, 17, 23, 30),
                datetime(2026, 11, 18, 6, 0),
                19.0,
            ),
            (
                TOMORROW,
                "plan",
                0,
                datetime(2026, 11, 19, 5, 40),
                datetime(2026, 11, 19, 6, 25),
                19.0,
            ),
        ],
    )
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))

    # When the heating poll runs
    with caplog.at_level(logging.WARNING, logger=APP_LOGGER_NAME):
        _retriever(mariadb_client, source).refresh(NOW)

    # Then Wednesday keeps what Hive holds (its minutes would land on the wrong
    # day), Thursday is replaced, and a WARNING names Wednesday
    written = _only(source.schedule_writes)
    assert written["wednesday"] == _hives_week()["wednesday"]
    assert written["thursday"][0] == {"start": 340, "value": {"target": 19.0}}
    assert any(
        record.levelno == logging.WARNING and "wednesday" in record.getMessage().lower()
        for record in caplog.records
    )


def test_a_slot_running_past_the_next_midnight_is_split_across_the_two_days(
    mariadb_client: MariaDBClient,
) -> None:
    # Given today's plan slot runs from 23:00 to 01:00 tomorrow (a window past
    # midnight), and tomorrow's own slot is the ordinary 05:40 to 06:25
    _seed_plan_slots(
        mariadb_client,
        [
            (
                TODAY,
                "plan",
                0,
                datetime(2026, 11, 18, 23, 0),
                datetime(2026, 11, 19, 1, 0),
                19.0,
            ),
            (
                TOMORROW,
                "plan",
                0,
                datetime(2026, 11, 19, 5, 40),
                datetime(2026, 11, 19, 6, 25),
                19.0,
            ),
        ],
    )
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))

    # When the heating poll runs
    _retriever(mariadb_client, source).refresh(NOW)

    # Then Wednesday is 19 C from 23:00 and the Away Temperature from 23:59, and
    # Thursday is 19 C from 00:00 to 01:00, then its own slot
    written = _only(source.schedule_writes)
    assert written["wednesday"] == [
        {"start": 1380, "value": {"target": 19.0}},
        {"start": 1439, "value": {"target": AWAY}},
    ]
    assert written["thursday"] == [
        {"start": 0, "value": {"target": 19.0}},
        {"start": 60, "value": {"target": AWAY}},
        {"start": 340, "value": {"target": 19.0}},
        {"start": 385, "value": {"target": AWAY}},
    ]


def test_yesterdays_slot_past_midnight_starts_todays_entries(
    mariadb_client: MariaDBClient,
) -> None:
    # Given yesterday's plan slot ran from 23:00 to 01:00 today, and today's own
    # slot is 05:40 to 06:25
    _seed_plan_slots(
        mariadb_client,
        [
            (
                TODAY - timedelta(days=1),
                "plan",
                0,
                datetime(2026, 11, 17, 23, 0),
                datetime(2026, 11, 18, 1, 0),
                19.0,
            ),
            (
                TODAY,
                "plan",
                0,
                datetime(2026, 11, 18, 5, 40),
                datetime(2026, 11, 18, 6, 25),
                19.0,
            ),
        ],
    )
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))

    # When the heating poll runs
    _retriever(mariadb_client, source).refresh(NOW)

    # Then Wednesday still starts with yesterday's tail, 19 C from 00:00 to 01:00
    written = _only(source.schedule_writes)
    assert written["wednesday"] == [
        {"start": 0, "value": {"target": 19.0}},
        {"start": 60, "value": {"target": AWAY}},
        {"start": 340, "value": {"target": 19.0}},
        {"start": 385, "value": {"target": AWAY}},
    ]


def test_a_day_with_no_plan_slot_keeps_hives_entries(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a plan slot for tomorrow (Thursday) only
    _seed_plan_slots(
        mariadb_client,
        [
            (
                TOMORROW,
                "plan",
                0,
                datetime(2026, 11, 19, 5, 40),
                datetime(2026, 11, 19, 6, 25),
                19.0,
            )
        ],
    )
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))

    # When the heating poll runs
    _retriever(mariadb_client, source).refresh(NOW)

    # Then Thursday is replaced and today's Wednesday keeps what Hive holds
    written = _only(source.schedule_writes)
    assert written["wednesday"] == _hives_week()["wednesday"]
    assert written["thursday"] == [
        {"start": 340, "value": {"target": 19.0}},
        {"start": 385, "value": {"target": AWAY}},
    ]


def test_a_refused_week_keeps_its_rule_in_the_log_and_the_stored_detail(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    # Given Hive's write is refused for a rule (a target off a half degree)
    _seed_todays_plan(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))
    source.schedule_error = WeekRuleError(
        "monday has a target that is not in half degrees"
    )

    # When the heating poll runs
    with caplog.at_level(logging.WARNING, logger=APP_LOGGER_NAME):
        _retriever(mariadb_client, source).refresh(NOW)

    # Then the rule is in the WARNING and in the stored failed action's detail, so
    # the household can act on it
    (action,) = _actions(mariadb_client)
    assert (action.outcome, action.detail) == (
        "failed",
        "ValueError: monday has a target that is not in half degrees",
    )
    assert "monday has a target that is not in half degrees" in caplog.text


def test_a_value_error_from_the_hive_session_is_stored_by_type_name_only(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    # Given a ValueError that did not come from a write rule, whose message holds
    # something that must not be stored (a library error can carry a URL or bytes)
    _seed_todays_plan(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))
    source.schedule_error = ValueError("https://secret.example/token=abc123")

    # When the heating poll runs
    with caplog.at_level(logging.WARNING, logger=APP_LOGGER_NAME):
        _retriever(mariadb_client, source).refresh(NOW)

    # Then only the type name is stored and logged
    (action,) = _actions(mariadb_client)
    assert (action.outcome, action.detail) == ("failed", "ValueError")
    assert "abc123" not in caplog.text


def test_a_dry_run_records_a_refusal_once_and_logs_its_rule(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    # Given Hive holds a Monday with a target off a half degree, which `on` would
    # refuse, and a plan for today
    _seed_todays_plan(mariadb_client)
    week = _hives_week()
    week["monday"][0]["value"]["target"] = 18.3
    source = _FakeHiveSource(mariadb_client, _status(week))
    retriever = _dry_run_retriever(mariadb_client, source)

    # When two polls run in a dry run
    with caplog.at_level(logging.WARNING, logger=APP_LOGGER_NAME):
        retriever.refresh(NOW)
        retriever.refresh(NOW)

    # Then one failed dry-run action is stored, with the rule as its detail, and
    # the rule is in a WARNING
    (action,) = _actions(mariadb_client)
    assert (action.outcome, action.dry_run, action.detail) == (
        "failed",
        True,
        "ValueError: monday has a target that is not in half degrees",
    )
    assert "monday has a target that is not in half degrees" in caplog.text
    assert not source.schedule_writes


def test_a_missing_plan_table_means_no_plan_and_no_write(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    # Given octopus-app has not created room_forecast_slot yet (nothing seeded)
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))

    # When the plan is read and the heating poll runs
    with caplog.at_level(logging.WARNING, logger=APP_LOGGER_NAME):
        slots = mariadb_client.read_plan_slots([TODAY, TOMORROW])
        _retriever(mariadb_client, source).refresh(NOW)

    # Then there are no plan slots, nothing is written, and it is not an error
    assert slots == []
    assert not source.schedule_writes
    assert caplog.text == ""


def test_a_day_with_no_plan_slot_is_logged_at_info(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    # Given a plan slot for tomorrow (Thursday) only, so today has none
    _seed_plan_slots(
        mariadb_client,
        [
            (
                TOMORROW,
                "plan",
                0,
                datetime(2026, 11, 19, 5, 40),
                datetime(2026, 11, 19, 6, 25),
                19.0,
            )
        ],
    )
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))

    # When the heating poll runs
    with caplog.at_level(logging.INFO, logger=APP_LOGGER_NAME):
        _retriever(mariadb_client, source).refresh(NOW)

    # Then the day that kept Hive's entries is named in an INFO line
    assert any(
        record.levelno == logging.INFO and "wednesday" in record.getMessage().lower()
        for record in caplog.records
    )


def test_a_database_error_in_the_comfort_schedule_does_not_stop_the_poll(
    mariadb_client: MariaDBClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given the house is empty, so the Away Override is due, and both controls are
    # on, but reading the plan fails with a database error
    mariadb_client.change_presence_state(NO_ONE_HOME, 0, NOW - timedelta(minutes=40))

    def _fails(_days: list[date]) -> list[Any]:
        raise OperationalError("SELECT", {}, Exception("connection lost"))

    monkeypatch.setattr(mariadb_client, "read_plan_slots", _fails)
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))
    retriever = HeatingRetriever(
        source,
        mariadb_client,
        control="on",
        comfort_control="on",
        away_temperature=AWAY,
    )

    # When the heating poll runs
    with caplog.at_level(logging.WARNING, logger=APP_LOGGER_NAME):
        retriever.refresh(NOW)

    # Then nothing is raised (a raise would retry the job and store a second poll
    # row), the Away Override still runs, and a WARNING names the error type only
    assert source.calls == ["away_override"]
    assert "OperationalError" in caplog.text
    assert "connection lost" not in caplog.text


def test_nothing_is_written_when_the_poll_could_not_read_hives_week(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    # Given a plan for today, but no readable weekly schedule this poll
    _seed_plan_slots(
        mariadb_client,
        [
            (
                TODAY,
                "plan",
                0,
                datetime(2026, 11, 18, 5, 40),
                datetime(2026, 11, 18, 6, 25),
                19.0,
            )
        ],
    )
    source = _FakeHiveSource(mariadb_client, _status(None))

    # When the heating poll runs
    with caplog.at_level(logging.WARNING, logger=APP_LOGGER_NAME):
        _retriever(mariadb_client, source).refresh(NOW)

    # Then nothing is written, and a WARNING says why
    assert not source.schedule_writes
    assert "weekly schedule" in caplog.text.lower()


def test_a_failed_write_is_stored_without_its_message_and_retried_next_poll(
    mariadb_client: MariaDBClient,
) -> None:
    # Given Hive rejects the write with an error whose message holds a secret
    _seed_todays_plan(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))
    source.schedule_error = RuntimeError("secret-token-123")
    retriever = _retriever(mariadb_client, source)

    # When the poll runs, then the next one runs with Hive answering
    retriever.refresh(NOW)
    source.schedule_error = None
    retriever.refresh(NOW)

    # Then the failure is stored by its exception type only and not raised, and the
    # next poll writes the week and records it sent
    failed, sent = _actions(mariadb_client)
    assert (failed.outcome, failed.detail) == ("failed", "RuntimeError")
    assert sent.outcome == "sent"
    assert len(source.schedule_writes) == 1


def test_the_comfort_schedule_is_written_before_schedule_is_restored_on_return(
    mariadb_client: MariaDBClient,
) -> None:
    # Given the house is empty with the Away Override applied, and the Comfort
    # Schedule and presence control are both on
    _seed_todays_plan(mariadb_client)
    mariadb_client.change_presence_state(NO_ONE_HOME, 0, NOW - timedelta(minutes=40))
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))
    retriever = HeatingRetriever(
        source,
        mariadb_client,
        control="on",
        comfort_control="on",
        away_temperature=AWAY,
    )
    retriever.refresh(NOW)
    assert "away_override" in source.calls
    source.calls.clear()

    # When someone comes home and the next poll runs, with the plan moved
    mariadb_client.change_presence_state(SOMEONE_HOME, 1, NOW)
    with mariadb_client.session_write_scope() as session:
        session.execute(text("UPDATE room_forecast_slot SET target = 20.0"))
    retriever.refresh(NOW + timedelta(minutes=2))

    # Then the schedule is written first and SCHEDULE is restored after it
    assert source.calls == ["comfort_schedule", "restore_schedule"]


@pytest.mark.parametrize(
    ("day", "weekday", "utc_start", "utc_end"),
    [
        # British Summer Time (UTC+1): 05:40 local is 04:40 UTC
        (
            date(2026, 10, 24),
            "saturday",
            datetime(2026, 10, 24, 4, 40),
            datetime(2026, 10, 24, 5, 25),
        ),
        # The day the clocks go back (01:00 BST becomes 01:00 GMT at 02:00 BST):
        # by 05:40 local it is GMT, so 05:40 local is 05:40 UTC
        (
            date(2026, 10, 25),
            "sunday",
            datetime(2026, 10, 25, 5, 40),
            datetime(2026, 10, 25, 6, 25),
        ),
    ],
    ids=["summer time", "clock-change day"],
)
def test_plan_instants_are_converted_at_their_own_london_offset(
    mariadb_client: MariaDBClient,
    day: date,
    weekday: str,
    utc_start: datetime,
    utc_end: datetime,
) -> None:
    # Given a plan slot of 05:40 to 06:25 London time, stored as naive UTC
    _seed_plan_slots(mariadb_client, [(day, "plan", 0, utc_start, utc_end, 19.0)])
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))
    now = datetime(day.year, day.month, day.day, 10, 0, tzinfo=UTC)

    # When the heating poll runs that day
    _retriever(mariadb_client, source).refresh(now)

    # Then the day's entries are the London clock minutes: 05:40 (340), 06:25 (385)
    written = _only(source.schedule_writes)
    assert written[weekday] == [
        {"start": 340, "value": {"target": 19.0}},
        {"start": 385, "value": {"target": AWAY}},
    ]


def test_a_start_rounds_earlier_and_an_end_rounds_later_to_whole_minutes(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a plan slot from 05:40:30 to 06:24:30, so it starts and ends part-way
    # through a minute
    _seed_plan_slots(
        mariadb_client,
        [
            (
                TODAY,
                "plan",
                0,
                datetime(2026, 11, 18, 5, 40, 30),
                datetime(2026, 11, 18, 6, 24, 30),
                19.0,
            )
        ],
    )
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))

    # When the heating poll runs
    _retriever(mariadb_client, source).refresh(NOW)

    # Then the start is 05:40 (minute 340, earlier) and the end 06:25 (385, later),
    # so the Comfort Window is never shortened
    written = _only(source.schedule_writes)
    assert [entry["start"] for entry in written["wednesday"]] == [340, 385]


_BASE_CONFIG: dict[str, Any] = {
    "hive": {"username": "someone@example.com", "password": "hunter2"},
    "mariadb": {
        "host": "localhost",
        "port": 3306,
        "database": "octopus",
        "username": "test",
        "password": "test",
    },
}


def test_the_configured_comfort_schedule_control_reaches_the_retriever(
    mariadb_client: MariaDBClient,
) -> None:
    # Given comfort_schedule.control is dry_run in the settings, and no presence
    settings = HiveApplicationSettings.model_validate(
        {**_BASE_CONFIG, "comfort_schedule": {"control": "dry_run"}}
    )
    _seed_todays_plan(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))

    # When the retriever built at startup polls
    build_heating_retriever(source, mariadb_client, settings).refresh(NOW)

    # Then a dry_run comfort_schedule action is stored and Hive is sent nothing
    (action,) = _actions(mariadb_client)
    assert (action.action, action.outcome) == ("comfort_schedule", "dry_run")
    assert not source.schedule_writes


def test_without_a_comfort_schedule_section_nothing_is_written_or_recorded(
    mariadb_client: MariaDBClient,
) -> None:
    # Given default settings (the Comfort Schedule is off) and a plan that differs
    settings = HiveApplicationSettings.model_validate(_BASE_CONFIG)
    _seed_todays_plan(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))

    # When the retriever built at startup polls
    build_heating_retriever(source, mariadb_client, settings).refresh(NOW)

    # Then nothing is written or recorded
    assert not source.schedule_writes
    assert not _actions(mariadb_client)


def _dry_run_retriever(
    mariadb: MariaDBClient, source: _FakeHiveSource
) -> HeatingRetriever:
    return HeatingRetriever(
        source, mariadb, comfort_control="dry_run", away_temperature=AWAY
    )


def _seed_todays_plan(mariadb: MariaDBClient, target: float = 19.0) -> None:
    _seed_plan_slots(
        mariadb,
        [
            (
                TODAY,
                "plan",
                0,
                datetime(2026, 11, 18, 5, 40),
                datetime(2026, 11, 18, 6, 25),
                target,
            )
        ],
    )


def test_a_dry_run_records_the_would_be_write_and_sends_nothing(
    mariadb_client: MariaDBClient,
) -> None:
    # Given the Comfort Schedule is in a dry run and today's plan differs from
    # Hive's week
    _seed_todays_plan(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))

    # When the heating poll runs
    _dry_run_retriever(mariadb_client, source).refresh(NOW)

    # Then Hive gets no call, and one dry_run comfort_schedule action is stored,
    # with a SHA-256 hex fingerprint of the built week in its detail
    assert not source.schedule_writes
    (action,) = _actions(mariadb_client)
    assert (action.action, action.outcome, action.dry_run) == (
        "comfort_schedule",
        "dry_run",
        True,
    )
    assert re.fullmatch(r"[0-9a-f]{64}", action.detail)


def test_a_dry_run_records_nothing_more_while_the_built_week_is_unchanged(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a dry run that has already recorded today's would-be write
    _seed_todays_plan(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))
    retriever = _dry_run_retriever(mariadb_client, source)
    retriever.refresh(NOW)

    # When the next poll runs with the same plan, and Hive still holds its own
    # week (nothing was written)
    retriever.refresh(NOW)

    # Then no second action is stored
    assert len(_actions(mariadb_client)) == 1


def test_a_dry_run_logs_the_would_be_week_only_when_it_records_it(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    # Given a dry run that has already logged and recorded today's would-be write
    _seed_todays_plan(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))
    retriever = _dry_run_retriever(mariadb_client, source)
    retriever.refresh(NOW)
    caplog.clear()

    # When the next poll runs with the same plan
    with caplog.at_level(logging.INFO, logger=APP_LOGGER_NAME):
        retriever.refresh(NOW)

    # Then nothing about the Comfort Schedule is logged again
    assert "Comfort Schedule" not in caplog.text


def test_a_dry_run_records_again_when_the_plan_changes(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a dry run that has recorded a plan of 19 C
    _seed_todays_plan(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(_hives_week()))
    retriever = _dry_run_retriever(mariadb_client, source)
    retriever.refresh(NOW)

    # When the plan moves to 20 C and the next poll runs
    with mariadb_client.session_write_scope() as session:
        session.execute(text("UPDATE room_forecast_slot SET target = 20.0"))
    retriever.refresh(NOW)

    # Then a second dry_run action is stored with a different fingerprint
    first, second = _actions(mariadb_client)
    assert second.outcome == "dry_run"
    assert second.detail != first.detail
