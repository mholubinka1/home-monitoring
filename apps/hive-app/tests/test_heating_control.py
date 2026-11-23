"""Presence-based heating control: HeatingRetriever holds the Away Override while
No One Is Home (spec: feature-presence-autonomous-heating, issue #803).

Driven through HeatingRetriever.refresh(now) with a fake HiveSource that records
the writes it is asked to make, and the real MariaDB test client underneath."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import OperationalError

from hive_app.common.logging import APP_LOGGER_NAME
from hive_app.data.heating import HeatingRetriever
from hive_app.data.model import HeatingStatus, HiveAuthState
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.presence import NO_ONE_HOME, SOMEONE_HOME, UNKNOWN

NOW = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
AWAY_TEMPERATURE = 7.0


class _FakeHiveSource:
    """A fake HiveSource: a settable HeatingStatus, a recorded list of writes, and
    the real MariaDBClient for storing the poll. Auth verbs are unused here."""

    def __init__(self, mariadb: MariaDBClient, status: HeatingStatus) -> None:
        self._mariadb = mariadb
        self.status = status
        self.writes: list[tuple[str, float | None]] = []
        self.write_error: Exception | None = None
        self.override_outcome = "applied"
        self.restore_outcomes = ["applied"]

    def fetch_heating_status(self) -> HeatingStatus:
        return self.status

    def persist_heating_status(self, status: HeatingStatus) -> None:
        self._mariadb.write_heating_status(status)

    def apply_away_override(self, temperature: float) -> str:
        self._record_write("away_override", temperature)
        return self.override_outcome

    def restore_schedule(self) -> str:
        self._record_write("restore_schedule", None)
        # Each restore uses the next scripted outcome; the last one repeats.
        if len(self.restore_outcomes) > 1:
            return self.restore_outcomes.pop(0)
        return self.restore_outcomes[0]

    def _record_write(self, action: str, temperature: float | None) -> None:
        if self.write_error is not None:
            raise self.write_error
        self.writes.append((action, temperature))

    def read_auth_state(self) -> HiveAuthState | None:
        raise NotImplementedError

    def login(self) -> HiveAuthState:
        raise NotImplementedError

    def resume(self, state: HiveAuthState) -> HiveAuthState:
        raise NotImplementedError

    def persist_auth_state(self, state: HiveAuthState) -> None:
        raise NotImplementedError


def _status(
    mode: str = "SCHEDULE",
    target: float = 19.0,
    boost_active: bool = False,
    online: bool | None = None,
) -> HeatingStatus:
    return HeatingStatus(
        polled_at=NOW,
        current_temp=18.0,
        target_temp=target,
        mode=mode,
        state="ON",
        boost_active=boost_active,
        boost_ends_at=None,
        schedule={},
        online=online,
    )


def _actions(mariadb: MariaDBClient) -> list[Any]:
    with mariadb.session_read_scope() as session:
        rows = session.query(model.heating_control_action).order_by(
            model.heating_control_action.id
        )
        session.expunge_all()
        return list(rows)


def _retriever(
    mariadb: MariaDBClient, source: _FakeHiveSource, control: str = "on"
) -> HeatingRetriever:
    return HeatingRetriever(
        source, mariadb, control=control, away_temperature=AWAY_TEMPERATURE
    )


def _no_one_home_for(mariadb: MariaDBClient, minutes: int = 40) -> None:
    mariadb.change_presence_state(NO_ONE_HOME, 0, NOW - timedelta(minutes=minutes))


def _someone_home(mariadb: MariaDBClient) -> None:
    mariadb.change_presence_state(SOMEONE_HOME, 1, NOW - timedelta(minutes=1))


def _poll(
    retriever: HeatingRetriever, source: _FakeHiveSource, status: HeatingStatus
) -> None:
    source.status = status
    retriever.refresh(NOW)


def _house_left_with_the_override_in_force(
    mariadb: MariaDBClient,
) -> tuple[_FakeHiveSource, HeatingRetriever]:
    """Given No One Is Home and the Away Override already applied."""
    _no_one_home_for(mariadb)
    source = _FakeHiveSource(mariadb, _status())
    retriever = _retriever(mariadb, source)
    retriever.refresh(NOW)
    source.writes.clear()
    return source, retriever


def test_away_override_is_applied_once_no_one_is_home(
    mariadb_client: MariaDBClient,
) -> None:
    _no_one_home_for(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(mode="SCHEDULE"))

    _retriever(mariadb_client, source).refresh(NOW)

    assert source.writes == [("away_override", AWAY_TEMPERATURE)]
    [action] = _actions(mariadb_client)
    assert action.action == "away_override"
    assert action.mode == "MANUAL"
    assert action.target == AWAY_TEMPERATURE
    assert action.dry_run is False
    assert action.outcome == "applied"


def test_no_away_override_is_sent_or_recorded_while_hive_reports_offline(
    mariadb_client: MariaDBClient,
) -> None:
    # Given No One Is Home and Hive reports the heating product offline
    _no_one_home_for(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(online=False))

    # When the heating poll runs
    _retriever(mariadb_client, source).refresh(NOW)

    # Then no Away Override is sent and no row is written
    assert not source.writes
    assert not _actions(mariadb_client)


def test_an_away_override_skipped_while_offline_is_sent_once_hive_is_online(
    mariadb_client: MariaDBClient,
) -> None:
    # Given an Away Override skipped while Hive reported the product offline
    _no_one_home_for(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(online=False))
    retriever = _retriever(mariadb_client, source)
    retriever.refresh(NOW)

    # When a later poll finds Hive online
    _poll(retriever, source, _status(online=True))

    # Then the Away Override is sent
    assert source.writes == [("away_override", AWAY_TEMPERATURE)]
    [action] = _actions(mariadb_client)
    assert (action.action, action.outcome) == ("away_override", "applied")


def test_nothing_is_written_when_the_thermostat_already_holds_the_away_temperature(
    mariadb_client: MariaDBClient,
) -> None:
    _no_one_home_for(mariadb_client)
    source = _FakeHiveSource(
        mariadb_client, _status(mode="MANUAL", target=AWAY_TEMPERATURE)
    )

    _retriever(mariadb_client, source).refresh(NOW)

    assert not source.writes
    [action] = _actions(mariadb_client)
    assert (action.action, action.outcome, action.detail) == (
        "away_override",
        "applied",
        "adopted",
    )


def test_nothing_is_adopted_in_dry_run_when_the_thermostat_already_holds_the_away_temperature(
    mariadb_client: MariaDBClient,
) -> None:
    _no_one_home_for(mariadb_client)
    source = _FakeHiveSource(
        mariadb_client, _status(mode="MANUAL", target=AWAY_TEMPERATURE)
    )

    _retriever(mariadb_client, source, control="dry_run").refresh(NOW)

    assert not source.writes
    assert not _actions(mariadb_client)


def test_an_adopted_away_temperature_is_restored_when_someone_comes_home(
    mariadb_client: MariaDBClient,
) -> None:
    _no_one_home_for(mariadb_client)
    source = _FakeHiveSource(
        mariadb_client, _status(mode="MANUAL", target=AWAY_TEMPERATURE)
    )
    retriever = _retriever(mariadb_client, source)
    retriever.refresh(NOW)
    _someone_home(mariadb_client)

    _poll(retriever, source, _status(mode="MANUAL", target=AWAY_TEMPERATURE))

    assert source.writes == [("restore_schedule", None)]


def test_an_override_that_hive_accepted_but_could_not_confirm_is_still_restored(
    mariadb_client: MariaDBClient,
) -> None:
    _no_one_home_for(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(mode="SCHEDULE"))
    source.override_outcome = "not_verified"
    retriever = _retriever(mariadb_client, source)
    retriever.refresh(NOW)
    source.writes.clear()
    _someone_home(mariadb_client)

    _poll(retriever, source, _status(mode="MANUAL", target=AWAY_TEMPERATURE))

    assert source.writes == [("restore_schedule", None)]


def test_a_restore_that_could_not_be_confirmed_is_attempted_again(
    mariadb_client: MariaDBClient,
) -> None:
    source, retriever = _house_left_with_the_override_in_force(mariadb_client)
    _someone_home(mariadb_client)
    source.restore_outcomes = ["not_verified", "applied"]
    at_away = _status(mode="MANUAL", target=AWAY_TEMPERATURE)

    _poll(retriever, source, at_away)
    _poll(retriever, source, at_away)
    _poll(retriever, source, at_away)

    assert source.writes == [
        ("restore_schedule", None),
        ("restore_schedule", None),
    ]


def test_with_control_off_a_failing_restore_is_tried_once_per_poll(
    mariadb_client: MariaDBClient,
) -> None:
    source, _ = _house_left_with_the_override_in_force(mariadb_client)
    _someone_home(mariadb_client)
    restarted = _retriever(mariadb_client, source, control="off")
    source.write_error = TimeoutError()

    _poll(restarted, source, _status(mode="MANUAL", target=AWAY_TEMPERATURE))

    failed = [a for a in _actions(mariadb_client) if a.outcome == "failed"]
    assert len(failed) == 1


def test_a_database_error_while_controlling_does_not_break_the_stored_poll(
    mariadb_client: MariaDBClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _no_one_home_for(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(mode="SCHEDULE"))
    monkeypatch.setattr(
        mariadb_client,
        "read_open_presence_period",
        Mock(side_effect=OperationalError("SELECT", {}, Exception("gone"))),
    )

    with caplog.at_level(logging.WARNING, logger=APP_LOGGER_NAME):
        _retriever(mariadb_client, source).refresh(NOW)

    with mariadb_client.session_read_scope() as session:
        assert session.query(model.heating_status).count() == 1
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_a_running_boost_is_not_interrupted_while_no_one_is_home(
    mariadb_client: MariaDBClient,
) -> None:
    _no_one_home_for(mariadb_client)
    source = _FakeHiveSource(
        mariadb_client, _status(mode="BOOST", target=22.0, boost_active=True)
    )

    _retriever(mariadb_client, source).refresh(NOW)

    assert not source.writes
    assert not _actions(mariadb_client)


def test_a_thermostat_switched_off_is_left_off_while_no_one_is_home(
    mariadb_client: MariaDBClient,
) -> None:
    _no_one_home_for(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(mode="OFF", target=1.0))

    _retriever(mariadb_client, source).refresh(NOW)

    assert not source.writes
    assert not _actions(mariadb_client)


def test_schedule_is_restored_when_someone_comes_home(
    mariadb_client: MariaDBClient,
) -> None:
    source, retriever = _house_left_with_the_override_in_force(mariadb_client)
    _someone_home(mariadb_client)

    _poll(retriever, source, _status(mode="MANUAL", target=AWAY_TEMPERATURE))

    assert source.writes == [("restore_schedule", None)]
    assert [a.action for a in _actions(mariadb_client)] == [
        "away_override",
        "restore_schedule",
    ]


def test_presence_unknown_keeps_the_override_for_nine_minutes_then_restores(
    mariadb_client: MariaDBClient,
) -> None:
    source, retriever = _house_left_with_the_override_in_force(mariadb_client)
    at_away = _status(mode="MANUAL", target=AWAY_TEMPERATURE)

    mariadb_client.change_presence_state(UNKNOWN, 0, NOW - timedelta(minutes=9))
    _poll(retriever, source, at_away)
    assert not source.writes

    mariadb_client.change_presence_state(UNKNOWN, 0, NOW - timedelta(minutes=10))
    _poll(retriever, source, at_away)
    assert source.writes == [("restore_schedule", None)]


def test_presence_unknown_with_no_earlier_period_counts_as_someone_home(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.change_presence_state(UNKNOWN, 0, NOW - timedelta(minutes=1))
    source = _FakeHiveSource(mariadb_client, _status(mode="SCHEDULE"))

    _retriever(mariadb_client, source).refresh(NOW)

    assert not source.writes
    assert not _actions(mariadb_client)


def test_restore_waits_for_a_running_boost_to_end(
    mariadb_client: MariaDBClient,
) -> None:
    source, retriever = _house_left_with_the_override_in_force(mariadb_client)
    _someone_home(mariadb_client)

    _poll(
        retriever,
        source,
        _status(mode="MANUAL", target=22.0, boost_active=True),
    )
    assert not source.writes

    _poll(retriever, source, _status(mode="MANUAL", target=AWAY_TEMPERATURE))
    assert source.writes == [("restore_schedule", None)]


def test_restore_is_skipped_when_the_thermostat_was_switched_off(
    mariadb_client: MariaDBClient,
) -> None:
    source, retriever = _house_left_with_the_override_in_force(mariadb_client)
    _someone_home(mariadb_client)

    _poll(retriever, source, _status(mode="OFF", target=1.0))
    _poll(retriever, source, _status(mode="OFF", target=1.0))

    assert not source.writes
    restores = [a for a in _actions(mariadb_client) if a.action == "restore_schedule"]
    assert [a.outcome for a in restores] == ["skipped"]


def test_the_household_stays_in_control_when_home_with_no_override_in_force(
    mariadb_client: MariaDBClient,
) -> None:
    _someone_home(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(mode="MANUAL", target=21.0))

    _retriever(mariadb_client, source).refresh(NOW)

    assert not source.writes
    assert not _actions(mariadb_client)


def test_a_write_that_raises_is_stored_as_failed_and_does_not_break_the_poll(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    _no_one_home_for(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(mode="SCHEDULE"))
    source.write_error = KeyError("climate")

    with caplog.at_level(logging.WARNING, logger=APP_LOGGER_NAME):
        _retriever(mariadb_client, source).refresh(NOW)

    [action] = _actions(mariadb_client)
    assert action.action == "away_override"
    assert action.outcome == "failed"
    assert action.detail == "KeyError"
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    with mariadb_client.session_read_scope() as session:
        assert session.query(model.heating_status).count() == 1


def test_an_override_whose_write_failed_after_landing_is_restored_on_return(
    mariadb_client: MariaDBClient,
) -> None:
    _no_one_home_for(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(mode="SCHEDULE"))
    retriever = _retriever(mariadb_client, source)
    source.write_error = TimeoutError()
    retriever.refresh(NOW)
    source.write_error = None
    _someone_home(mariadb_client)

    _poll(retriever, source, _status(mode="MANUAL", target=AWAY_TEMPERATURE))

    assert source.writes == [("restore_schedule", None)]


def test_an_override_whose_write_failed_after_landing_is_restored_once_control_is_off(
    mariadb_client: MariaDBClient,
) -> None:
    _no_one_home_for(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(mode="SCHEDULE"))
    source.write_error = TimeoutError()
    _retriever(mariadb_client, source).refresh(NOW)
    source.write_error = None
    restarted = _retriever(mariadb_client, source, control="off")

    _poll(restarted, source, _status(mode="MANUAL", target=AWAY_TEMPERATURE))

    assert source.writes == [("restore_schedule", None)]


def test_a_failed_restore_is_attempted_again_at_the_next_poll(
    mariadb_client: MariaDBClient,
) -> None:
    source, retriever = _house_left_with_the_override_in_force(mariadb_client)
    _someone_home(mariadb_client)
    at_away = _status(mode="MANUAL", target=AWAY_TEMPERATURE)
    source.write_error = TimeoutError()

    _poll(retriever, source, at_away)
    source.write_error = None
    _poll(retriever, source, at_away)

    assert source.writes == [("restore_schedule", None)]
    outcomes = [
        a.outcome for a in _actions(mariadb_client) if a.action == "restore_schedule"
    ]
    assert outcomes == ["failed", "applied"]


def test_a_dry_run_absence_records_one_override_and_one_restore_and_calls_hive_never(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    _no_one_home_for(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(mode="SCHEDULE"))
    retriever = _retriever(mariadb_client, source, control="dry_run")
    on_schedule = _status(mode="SCHEDULE")

    with caplog.at_level(logging.INFO, logger=APP_LOGGER_NAME):
        for _ in range(5):
            _poll(retriever, source, on_schedule)
        _someone_home(mariadb_client)
        for _ in range(5):
            _poll(retriever, source, on_schedule)

    assert not source.writes
    actions = _actions(mariadb_client)
    assert [(a.action, a.dry_run, a.outcome) for a in actions] == [
        ("away_override", True, "dry_run"),
        ("restore_schedule", True, "dry_run"),
    ]
    assert any("would set" in r.getMessage() for r in caplog.records)


def test_an_unknown_control_setting_is_refused_rather_than_treated_as_on(
    mariadb_client: MariaDBClient,
) -> None:
    source = _FakeHiveSource(mariadb_client, _status())

    with pytest.raises(ValueError, match="dryrun"):
        HeatingRetriever(source, mariadb_client, control="dryrun")


def test_with_control_off_nothing_is_written_or_recorded_while_no_one_is_home(
    mariadb_client: MariaDBClient,
) -> None:
    _no_one_home_for(mariadb_client)
    source = _FakeHiveSource(mariadb_client, _status(mode="SCHEDULE"))

    _retriever(mariadb_client, source, control="off").refresh(NOW)

    assert not source.writes
    assert not _actions(mariadb_client)


@pytest.mark.parametrize("control", ["dry_run", "off"])
def test_a_real_override_is_restored_once_after_control_leaves_on(
    mariadb_client: MariaDBClient, control: str
) -> None:
    source, _ = _house_left_with_the_override_in_force(mariadb_client)
    restarted = _retriever(mariadb_client, source, control=control)
    at_away = _status(mode="MANUAL", target=AWAY_TEMPERATURE)

    for _ in range(3):
        _poll(restarted, source, at_away)

    assert source.writes == [("restore_schedule", None)]
    real_actions = [a for a in _actions(mariadb_client) if not a.dry_run]
    assert [(a.action, a.outcome) for a in real_actions] == [
        ("away_override", "applied"),
        ("restore_schedule", "applied"),
    ]


def test_a_failed_leaving_on_restore_is_retried_then_not_repeated(
    mariadb_client: MariaDBClient,
) -> None:
    source, _ = _house_left_with_the_override_in_force(mariadb_client)
    restarted = _retriever(mariadb_client, source, control="off")
    at_away = _status(mode="MANUAL", target=AWAY_TEMPERATURE)
    source.write_error = TimeoutError()

    _poll(restarted, source, at_away)
    source.write_error = None
    _poll(restarted, source, at_away)
    _poll(restarted, source, at_away)

    assert source.writes == [("restore_schedule", None)]


def test_away_override_is_reasserted_over_a_hand_change(
    mariadb_client: MariaDBClient,
) -> None:
    source, retriever = _house_left_with_the_override_in_force(mariadb_client)

    _poll(retriever, source, _status(mode="MANUAL", target=21.0))

    assert source.writes == [("away_override", AWAY_TEMPERATURE)]
