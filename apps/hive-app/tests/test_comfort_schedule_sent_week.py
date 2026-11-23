"""The Sent Week: a Comfort Schedule write counts only once a later poll reads it
(spec: bugfix-comfort-schedule-write-only-while-hive-online, issue #826).

Driven through HeatingRetriever.refresh(now) with the fake HiveSource and helpers
of test_comfort_schedule_write, and the real MariaDB test client underneath.
London is on GMT in November. The made-up slot is 05:40 to 06:25 at 19 C."""

import logging
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text

from hive_app.common.logging import APP_LOGGER_NAME
from hive_app.data.mysql.client import MariaDBClient

from .test_comfort_schedule_write import NOW
from .test_comfort_schedule_write import _actions as actions
from .test_comfort_schedule_write import _FakeHiveSource as FakeHiveSource
from .test_comfort_schedule_write import _hives_week as hives_week
from .test_comfort_schedule_write import _only as only
from .test_comfort_schedule_write import _retriever as retriever_for
from .test_comfort_schedule_write import _seed_todays_plan as seed_todays_plan
from .test_comfort_schedule_write import _status as status_of


def _as_hive_returns(week: dict[str, Any]) -> dict[str, Any]:
    """The week as Hive returns it: a whole-number target as an int (19, not
    19.0), as the live account does."""
    return {
        day: [
            {
                "start": entry["start"],
                "value": {
                    "target": (
                        int(entry["value"]["target"])
                        if float(entry["value"]["target"]).is_integer()
                        else entry["value"]["target"]
                    )
                },
            }
            for entry in entries
        ]
        for day, entries in week.items()
    }


def test_a_sent_week_a_later_poll_reads_is_recorded_applied(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a sent week
    seed_todays_plan(mariadb_client)
    source = FakeHiveSource(mariadb_client, status_of(hives_week()))
    retriever = retriever_for(mariadb_client, source)
    retriever.refresh(NOW)
    sent_week = only(source.schedule_writes)

    # When a later poll reads that week from Hive, with whole-number targets
    source.status = status_of(_as_hive_returns(sent_week))
    retriever.refresh(NOW + timedelta(minutes=2))

    # Then applied is recorded with the sent week's fingerprint, and nothing more
    # is posted
    sent, applied = actions(mariadb_client)
    assert (sent.outcome, applied.outcome) == ("sent", "applied")
    assert applied.detail == sent.detail
    assert len(source.schedule_writes) == 1


def test_a_sent_week_hive_has_not_shown_is_resent_with_no_new_row(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a sent week Hive has not shown yet (it still reports its own week)
    seed_todays_plan(mariadb_client)
    source = FakeHiveSource(mariadb_client, status_of(hives_week()))
    retriever = retriever_for(mariadb_client, source)
    retriever.refresh(NOW)

    # When the next online poll runs
    retriever.refresh(NOW + timedelta(minutes=2))

    # Then the same week is resent and no new row is written
    assert len(source.schedule_writes) == 2
    assert source.schedule_writes[1] == source.schedule_writes[0]
    (sent,) = actions(mariadb_client)
    assert sent.outcome == "sent"


def test_a_plan_change_while_a_week_is_pending_posts_the_new_week(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a sent week of 19 C Hive has not shown
    seed_todays_plan(mariadb_client)
    source = FakeHiveSource(mariadb_client, status_of(hives_week()))
    retriever = retriever_for(mariadb_client, source)
    retriever.refresh(NOW)

    # When the plan moves to 20 C and the next online poll runs
    with mariadb_client.session_write_scope() as session:
        session.execute(text("UPDATE room_forecast_slot SET target = 20.0"))
    retriever.refresh(NOW + timedelta(minutes=2))

    # Then the new week is posted and recorded sent with its own fingerprint
    new_week = source.schedule_writes[1]
    assert new_week["wednesday"][0] == {"start": 340, "value": {"target": 20.0}}
    first, second = actions(mariadb_client)
    assert (first.outcome, second.outcome) == ("sent", "sent")
    assert second.detail != first.detail


def test_nothing_is_posted_or_recorded_while_hive_reports_the_product_offline(
    mariadb_client: MariaDBClient,
) -> None:
    # Given Hive reports the heating product offline at the poll, and the built
    # week differs from Hive's
    seed_todays_plan(mariadb_client)
    status = status_of(hives_week())
    status.online = False
    source = FakeHiveSource(mariadb_client, status)

    # When the heating poll runs
    retriever_for(mariadb_client, source).refresh(NOW)

    # Then nothing is posted and no row is written
    assert not source.schedule_writes
    assert not actions(mariadb_client)


def test_no_row_is_written_when_the_writes_own_session_finds_hive_offline(
    mariadb_client: MariaDBClient,
) -> None:
    # Given the poll found Hive online, but the write's own session finds the
    # heating product offline (it flapped in between)
    seed_todays_plan(mariadb_client)
    source = FakeHiveSource(mariadb_client, status_of(hives_week()))
    source.schedule_outcome = "offline"

    # When the heating poll runs
    retriever_for(mariadb_client, source).refresh(NOW)

    # Then no row is written, so the next online poll posts afresh
    assert not actions(mariadb_client)


def test_a_sent_week_unshown_after_15_minutes_is_not_verified_once_and_resent(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    # Given a sent week Hive has not shown at the polls of the next 14 minutes
    seed_todays_plan(mariadb_client)
    source = FakeHiveSource(mariadb_client, status_of(hives_week()))
    retriever = retriever_for(mariadb_client, source)
    retriever.refresh(NOW)
    retriever.refresh(NOW + timedelta(minutes=14))

    # When the online polls 15 and 17 minutes after it was sent run
    with caplog.at_level(logging.WARNING, logger=APP_LOGGER_NAME):
        retriever.refresh(NOW + timedelta(minutes=15))
        retriever.refresh(NOW + timedelta(minutes=17))

    # Then not_verified is recorded once, with its fingerprint and a WARNING, and
    # the week went on being resent at every poll
    sent, not_verified = actions(mariadb_client)
    assert (sent.outcome, not_verified.outcome) == ("sent", "not_verified")
    assert not_verified.detail == sent.detail
    assert caplog.text.lower().count("not verified") == 1
    assert len(source.schedule_writes) == 4


def test_nothing_is_posted_when_hive_holds_the_week_in_another_entry_order(
    mariadb_client: MariaDBClient,
) -> None:
    # Given Hive already holds today's plan, but lists Wednesday's entries in the
    # reverse order (Away from 06:25 first, then 19 C from 05:40)
    seed_todays_plan(mariadb_client)
    week = hives_week()
    week["wednesday"] = [
        {"start": 385, "value": {"target": 7}},  # the tests' Away Temperature
        {"start": 340, "value": {"target": 19}},
    ]
    source = FakeHiveSource(mariadb_client, status_of(week))

    # When the heating poll runs
    retriever_for(mariadb_client, source).refresh(NOW)

    # Then nothing is posted and nothing is recorded: it is the same week
    assert not source.schedule_writes
    assert not actions(mariadb_client)


def test_a_malformed_hive_week_while_a_week_is_pending_does_not_stop_the_poll(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a sent week, and Hive then returning a week whose Monday has an entry
    # with no target
    seed_todays_plan(mariadb_client)
    source = FakeHiveSource(mariadb_client, status_of(hives_week()))
    retriever = retriever_for(mariadb_client, source)
    retriever.refresh(NOW)
    malformed = hives_week()
    malformed["monday"].append({"start": 600})
    source.status = status_of(malformed)

    # When the next poll runs
    retriever.refresh(NOW + timedelta(minutes=2))

    # Then the poll completes without raising and nothing is recorded applied
    outcomes = [a.outcome for a in actions(mariadb_client)]
    assert "applied" not in outcomes


def test_a_resend_that_raises_keeps_the_week_pending_for_its_landing(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a sent week, and its resend at the next poll raising (Hive answered 500)
    seed_todays_plan(mariadb_client)
    source = FakeHiveSource(mariadb_client, status_of(hives_week()))
    retriever = retriever_for(mariadb_client, source)
    retriever.refresh(NOW)
    sent_week = only(source.schedule_writes)
    source.schedule_error = RuntimeError("HTTP 500")
    retriever.refresh(NOW + timedelta(minutes=2))

    # When a later poll reads the sent week from Hive
    source.schedule_error = None
    source.status = status_of(_as_hive_returns(sent_week))
    retriever.refresh(NOW + timedelta(minutes=4))

    # Then the failed resend left no row, and the landing is recorded applied
    assert [a.outcome for a in actions(mariadb_client)] == ["sent", "applied"]


def test_days_keeping_hives_entries_are_logged_once_not_on_every_resend(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    # Given today's plan only, so tomorrow (Thursday) keeps Hive's entries
    seed_todays_plan(mariadb_client)
    source = FakeHiveSource(mariadb_client, status_of(hives_week()))
    retriever = retriever_for(mariadb_client, source)

    # When the week is posted, then resent at the next poll (Hive not showing it)
    with caplog.at_level(logging.INFO, logger=APP_LOGGER_NAME):
        retriever.refresh(NOW)
        retriever.refresh(NOW + timedelta(minutes=2))

    # Then Thursday is logged once, on the poll that posted
    assert len(source.schedule_writes) == 2
    assert caplog.text.count("no plan slot for thursday") == 1
