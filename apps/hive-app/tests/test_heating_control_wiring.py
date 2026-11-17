"""Startup wiring for presence-based heating control (issue #803): the settings
reach HeatingRetriever, and the control-action table is created at startup."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from hive_app.common.config import HiveApplicationSettings
from hive_app.data.model import HeatingStatus, HiveAuthState
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.presence import NO_ONE_HOME
from hive_app.main import build_heating_retriever

NOW = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)

BASE_CONFIG: dict[str, Any] = {
    "hive": {"username": "someone@example.com", "password": "hunter2"},
    "mariadb": {
        "host": "localhost",
        "port": 3306,
        "database": "octopus",
        "username": "test",
        "password": "test",
    },
}
PRESENCE = {"pihole": {"url": "http://pihole.example.lan", "password": "pw"}}


class _FakeHiveSource:
    def __init__(self, mariadb: MariaDBClient) -> None:
        self._mariadb = mariadb
        self.writes: list[str] = []

    def fetch_heating_status(self) -> HeatingStatus:
        return HeatingStatus(
            polled_at=NOW,
            current_temp=18.0,
            target_temp=19.0,
            mode="SCHEDULE",
            state="ON",
            boost_active=False,
            boost_ends_at=None,
            schedule={},
        )

    def persist_heating_status(self, status: HeatingStatus) -> None:
        self._mariadb.write_heating_status(status)

    def apply_away_override(self, _temperature: float) -> str:
        self.writes.append("away_override")
        return "applied"

    def restore_schedule(self) -> str:
        self.writes.append("restore_schedule")
        return "applied"

    def read_auth_state(self) -> HiveAuthState | None:
        raise NotImplementedError

    def login(self) -> HiveAuthState:
        raise NotImplementedError

    def resume(self, state: HiveAuthState) -> HiveAuthState:
        raise NotImplementedError

    def persist_auth_state(self, state: HiveAuthState) -> None:
        raise NotImplementedError


def _absent_for_forty_minutes(mariadb: MariaDBClient) -> None:
    mariadb.change_presence_state(NO_ONE_HOME, 0, NOW - timedelta(minutes=40))


def test_presence_dry_run_and_the_configured_away_temperature_reach_the_retriever(
    mariadb_client: MariaDBClient,
) -> None:
    settings = HiveApplicationSettings.model_validate(
        {
            **BASE_CONFIG,
            "presence": {**PRESENCE, "control": "dry_run"},
            "comfort_schedule": {"away_temperature": 12.0},
        }
    )
    _absent_for_forty_minutes(mariadb_client)
    source = _FakeHiveSource(mariadb_client)

    build_heating_retriever(source, mariadb_client, settings).refresh(NOW)

    with mariadb_client.session_read_scope() as session:
        [action] = session.query(model.heating_control_action).all()
        assert (action.action, action.target, action.outcome) == (
            "away_override",
            12.0,
            "dry_run",
        )
    assert not source.writes


def test_without_a_presence_section_heating_control_is_off(
    mariadb_client: MariaDBClient,
) -> None:
    settings = HiveApplicationSettings.model_validate(BASE_CONFIG)
    _absent_for_forty_minutes(mariadb_client)
    source = _FakeHiveSource(mariadb_client)

    build_heating_retriever(source, mariadb_client, settings).refresh(NOW)

    assert not source.writes
    with mariadb_client.session_read_scope() as session:
        assert session.query(model.heating_control_action).count() == 0


def test_startup_creates_the_heating_control_action_table_when_it_is_missing(
    unsynced_mariadb_client_factory: Callable[[], MariaDBClient],
) -> None:
    client = unsynced_mariadb_client_factory()

    client.write_heating_control_action(
        NOW, "away_override", "MANUAL", 7.0, False, "applied"
    )

    with client.session_read_scope() as session:
        assert session.query(model.heating_control_action).count() == 1
