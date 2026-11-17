import logging.config
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from logging import Logger, getLogger
from typing import Protocol

from sqlalchemy.exc import SQLAlchemyError

from hive_app.common.logging import APP_LOGGER_NAME, config
from hive_app.data.model import HeatingStatus, HiveAuthState
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.mysql.model import (
    ACTION_AWAY_OVERRIDE,
    ACTION_RESTORE_SCHEDULE,
    OUTCOME_APPLIED,
    OUTCOME_DRY_RUN,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
)
from hive_app.data.presence import NO_ONE_HOME, SOMEONE_HOME, UNKNOWN

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

UNKNOWN_HOLD = timedelta(minutes=10)

CONTROL_OFF = "off"
CONTROL_DRY_RUN = "dry_run"
CONTROL_ON = "on"
MODE_MANUAL = "MANUAL"
MODE_SCHEDULE = "SCHEDULE"
MODE_OFF = "OFF"


class HiveSource(Protocol):
    def fetch_heating_status(self) -> HeatingStatus: ...

    def persist_heating_status(self, status: HeatingStatus) -> None: ...

    def apply_away_override(self, temperature: float) -> str: ...

    def restore_schedule(self) -> str: ...

    def read_auth_state(self) -> HiveAuthState | None: ...

    def login(self) -> HiveAuthState: ...

    def resume(self, state: HiveAuthState) -> HiveAuthState: ...

    def persist_auth_state(self, state: HiveAuthState) -> None: ...


@dataclass(frozen=True)
class _Write:
    """What a heating write sets, as stored in heating_control_action."""

    action: str
    mode: str
    target: float | None


class PresenceHeatingControl:
    """Applies the presence control policy to the Hive heating after each poll.
    It is only built with a MariaDB client, so one is always there to use."""

    def __init__(
        self,
        client: HiveSource,
        mariadb: MariaDBClient,
        control: str,
        away_temperature: float,
    ) -> None:
        self._client = client
        self._mariadb = mariadb
        self._control = control
        self._away_temperature = away_temperature
        self._dry_run = control == CONTROL_DRY_RUN

    def apply(self, now: datetime, status: HeatingStatus) -> None:
        """Apply the policy. A database error is logged and not raised: the
        heating status is already stored, and a raise would retry the job and
        store a second row."""
        try:
            self._apply(now, status)
        except SQLAlchemyError as error:
            logger.warning(
                "Heating control skipped after a database error: %s",
                type(error).__name__,
            )

    def _apply(self, now: datetime, status: HeatingStatus) -> None:
        if self._control != CONTROL_ON:
            self._restore_real_override_once(now, status)
        presence = self._presence_for_control(now)
        if presence == NO_ONE_HOME:
            self._hold_away_override(now, status)
        elif presence == SOMEONE_HOME:
            self._return_to_schedule(now, status)

    def _hold_away_override(self, now: datetime, status: HeatingStatus) -> None:
        """No One Is Home: set the away temperature unless control is off, the
        heating is boosted or off, already at the away temperature, or a dry run
        has already recorded it."""
        if self._control == CONTROL_OFF or self._leaves_heating_alone(status):
            return
        if self._already_at_away_temperature(status):
            self._adopt_away_temperature(now)
            return
        if self._dry_run and self._mariadb.read_away_override_in_force(True):
            return
        self._write(
            now,
            _Write(ACTION_AWAY_OVERRIDE, MODE_MANUAL, self._away_temperature),
            lambda: self._client.apply_away_override(self._away_temperature),
            self._dry_run,
        )

    def _adopt_away_temperature(self, now: datetime) -> None:
        """The thermostat already holds the away temperature with no override in
        force (a write that failed after landing, or a hand change): with control
        on, record it as applied so the return restores it. Nothing is sent."""
        if self._control != CONTROL_ON or self._mariadb.read_away_override_in_force(
            False
        ):
            return
        self._mariadb.write_heating_control_action(
            now,
            ACTION_AWAY_OVERRIDE,
            MODE_MANUAL,
            self._away_temperature,
            False,
            OUTCOME_APPLIED,
            "adopted",
        )

    def _return_to_schedule(self, now: datetime, status: HeatingStatus) -> None:
        """Someone Is Home: end an away override in force, unless boosted. With
        control off, the once-per-poll real restore in apply has already done it."""
        if status.boost_active or self._control == CONTROL_OFF:
            return
        if self._mariadb.read_away_override_in_force(self._dry_run):
            self._restore_schedule(now, status, self._dry_run)

    def _restore_real_override_once(self, now: datetime, status: HeatingStatus) -> None:
        """Control is not `on`, so a real away override left in force is restored
        as for Someone Is Home, whoever is home now. A failed restore stays in
        force and is retried at the next poll; once restored this does nothing."""
        if status.boost_active:
            return
        if self._mariadb.read_away_override_in_force(False):
            self._restore_schedule(now, status, False)

    def _restore_schedule(
        self, now: datetime, status: HeatingStatus, dry_run: bool
    ) -> None:
        if status.mode == MODE_OFF:
            self._mariadb.write_heating_control_action(
                now, ACTION_RESTORE_SCHEDULE, None, None, dry_run, OUTCOME_SKIPPED
            )
            return
        self._write(
            now,
            _Write(ACTION_RESTORE_SCHEDULE, MODE_SCHEDULE, None),
            self._client.restore_schedule,
            dry_run,
        )

    @staticmethod
    def _leaves_heating_alone(status: HeatingStatus) -> bool:
        return status.boost_active or status.mode == MODE_OFF

    def _already_at_away_temperature(self, status: HeatingStatus) -> bool:
        return (
            status.mode == MODE_MANUAL and status.target_temp == self._away_temperature
        )

    def _write(
        self,
        now: datetime,
        write: "_Write",
        send: Callable[[], str],
        dry_run: bool,
    ) -> None:
        """Send one write and store its outcome; a failure is stored as `failed`
        with the exception type name only, and never raised out of the poll."""
        action, mode, target = write.action, write.mode, write.target
        detail = None
        if dry_run:
            logger.info("Heating control would set %s: %s %s", action, mode, target)
            self._mariadb.write_heating_control_action(
                now, action, mode, target, True, OUTCOME_DRY_RUN
            )
            return
        try:
            outcome = send()
        except Exception as error:
            outcome, detail = OUTCOME_FAILED, type(error).__name__
            logger.warning("Heating control write %s failed: %s", action, detail)
        self._mariadb.write_heating_control_action(
            now, action, mode, target, False, outcome, detail
        )

    def _presence_for_control(self, now: datetime) -> str | None:
        """The open period's state, except Presence Unknown: unknown for under
        UNKNOWN_HOLD counts as the state before it; from UNKNOWN_HOLD, or with no
        earlier period, it counts as Someone Is Home."""
        period = self._mariadb.read_open_presence_period()
        if period is None:
            return None
        if period[0] != UNKNOWN:
            return period[0]
        before = self._mariadb.read_presence_state_before(period[1])
        if before is None or now - period[1] >= UNKNOWN_HOLD:
            return SOMEONE_HOME
        return before


class HeatingRetriever:
    _client: HiveSource

    def __init__(
        self,
        client: HiveSource,
        mariadb: MariaDBClient | None = None,
        control: str = CONTROL_OFF,
        away_temperature: float = 7.0,
    ) -> None:
        self._client = client
        self._presence_control = (
            PresenceHeatingControl(client, mariadb, control, away_temperature)
            if mariadb is not None
            else None
        )

    def refresh(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        status = self._client.fetch_heating_status()
        self._client.persist_heating_status(status)
        if self._presence_control is not None:
            self._presence_control.apply(now, status)
