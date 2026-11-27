import logging.config
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from logging import Logger, getLogger
from typing import Any, Protocol

from sqlalchemy.exc import SQLAlchemyError

from common.local_day import to_local_date
from hive_app.common.logging import APP_LOGGER_NAME, config
from hive_app.data.comfort_schedule import (
    REASON_NO_SLOT,
    WeekRuleError,
    build_week,
    fingerprint_week,
    validate_week,
)
from hive_app.data.model import HeatingStatus, HiveAuthState
from hive_app.data.mysql.client import ComfortScheduleAction, MariaDBClient
from hive_app.data.mysql.model import (
    ACTION_AWAY_OVERRIDE,
    ACTION_COMFORT_SCHEDULE,
    ACTION_RESTORE_SCHEDULE,
    OUTCOME_APPLIED,
    OUTCOME_DRY_RUN,
    OUTCOME_FAILED,
    OUTCOME_NOT_VERIFIED,
    OUTCOME_OFFLINE,
    OUTCOME_SENT,
    OUTCOME_SKIPPED,
)
from hive_app.data.presence import NO_ONE_HOME, SOMEONE_HOME, UNKNOWN

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

UNKNOWN_HOLD = timedelta(minutes=10)
NOT_VERIFIED_AFTER = timedelta(minutes=15)
OFFLINE_MESSAGE = "Comfort Schedule not written: Hive reports the product offline"
# heating_control_action.detail is a String(255).
DETAIL_MAX_LENGTH = 255

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

    def write_comfort_schedule(self, week: dict[str, Any]) -> str: ...

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
        if self._dry_run and self._mariadb.read_away_override_in_force(dry_run=True):
            return
        self._write(
            now,
            _Write(ACTION_AWAY_OVERRIDE, MODE_MANUAL, self._away_temperature),
            lambda: self._client.apply_away_override(self._away_temperature),
            self._dry_run,
            status,
        )

    def _adopt_away_temperature(self, now: datetime) -> None:
        """The thermostat already holds the away temperature with no override in
        force (a hand-set MANUAL on the Away Temperature): with control on, record
        it as applied so the return restores it. Nothing is sent."""
        if self._control != CONTROL_ON or self._mariadb.read_away_override_in_force(
            dry_run=False
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
        if self._mariadb.read_away_override_in_force(dry_run=self._dry_run):
            self._restore_schedule(now, status, self._dry_run)

    def _restore_real_override_once(self, now: datetime, status: HeatingStatus) -> None:
        """Control is not `on`, so a real away override left in force is restored
        as for Someone Is Home, whoever is home now. A failed restore stays in
        force and is retried at the next poll; once restored this does nothing."""
        if status.boost_active:
            return
        if self._mariadb.read_away_override_in_force(dry_run=False):
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
            status,
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
        write: _Write,
        send: Callable[[], str],
        dry_run: bool,
        status: HeatingStatus,
    ) -> None:
        """Send one write and store its outcome; a failure is stored as `failed`
        with the exception type name only, and never raised out of the poll. A
        real write is skipped while Hive reports the product offline, and tried
        again at the next poll."""
        action, mode, target = write.action, write.mode, write.target
        detail = None
        if dry_run:
            logger.info("Heating control would set %s: %s %s", action, mode, target)
            self._mariadb.write_heating_control_action(
                now, action, mode, target, True, OUTCOME_DRY_RUN
            )
            return
        if status.online is False:
            logger.debug(
                "Heating control %s not sent: Hive reports the product offline",
                action,
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


def _failure_detail(error: Exception) -> str:
    """What is stored and logged of a failed write: the type name only, as the
    message may hold secrets. A WeekRuleError is a write-rule refusal, whose message
    holds only a weekday and a rule, so it is kept."""
    if isinstance(error, WeekRuleError):
        return f"ValueError: {error}"[:DETAIL_MAX_LENGTH]
    return type(error).__name__


class ComfortScheduleControl:
    """Writes the Comfort Plan into Hive's weekly schedule after each poll: for
    real when control is `on`, or recorded only when it is `dry_run`."""

    def __init__(
        self,
        client: HiveSource,
        mariadb: MariaDBClient,
        control: str,
        away_temperature: float,
    ) -> None:
        self._client = client
        self._mariadb = mariadb
        self._dry_run = control == CONTROL_DRY_RUN
        self._away_temperature = away_temperature

    def apply(self, now: datetime, status: HeatingStatus) -> None:
        """Apply the Comfort Schedule. A database error is logged and not
        raised: the heating status is already stored, and a raise would retry
        the job, store a second row and stop presence control running."""
        try:
            self._apply(now, status)
        except SQLAlchemyError as error:
            logger.warning(
                "Comfort Schedule skipped after a database error: %s",
                type(error).__name__,
            )

    def _apply(self, now: datetime, status: HeatingStatus) -> None:
        if status.weekly_schedule is None:
            logger.warning(
                "Comfort Schedule not written: this poll could not read Hive's "
                "weekly schedule."
            )
            return
        if not self._dry_run and self._confirm_pending(now, status.weekly_schedule):
            return
        week, kept_days = self._build_week(now, status.weekly_schedule)
        # Fingerprints, as for confirming: the same week however Hive orders or
        # types its entries.
        if fingerprint_week(week) == fingerprint_week(status.weekly_schedule):
            return
        if self._dry_run:
            self._record_dry_run(now, week, kept_days)
        elif status.online is False:
            logger.debug(OFFLINE_MESSAGE)
        else:
            self._write_week(now, week, kept_days, self._pending_row(week))

    def _confirm_pending(self, now: datetime, hive_week: dict[str, Any]) -> bool:
        """Record `applied` when the newest real row is pending (sent or not
        verified) and Hive's week now has its fingerprint."""
        pending = self._pending_row(hive_week)
        if pending is None:
            return False
        self._mariadb.write_heating_control_action(
            now,
            ACTION_COMFORT_SCHEDULE,
            None,
            None,
            False,
            OUTCOME_APPLIED,
            pending.detail,
        )
        return True

    def _build_week(
        self, now: datetime, hive_week: dict[str, Any]
    ) -> tuple[dict[str, Any], list[tuple[str, str]]]:
        today = to_local_date(now)
        slots = self._mariadb.read_plan_slots(
            [today - timedelta(days=1), today, today + timedelta(days=1)]
        )
        return build_week(hive_week, slots, today, self._away_temperature)

    @staticmethod
    def _log_kept_days(kept_days: list[tuple[str, str]]) -> None:
        for day, reason in kept_days:
            if reason == REASON_NO_SLOT:
                logger.info(
                    "Comfort Schedule: no plan slot for %s, keeping Hive's entries.",
                    day,
                )
            else:
                logger.warning(
                    "Comfort Schedule: %s keeps Hive's entries, as %s.", day, reason
                )

    def _record_dry_run(
        self, now: datetime, week: dict[str, Any], kept_days: list[tuple[str, str]]
    ) -> None:
        """Store what would be written, once per distinct week. A week the write
        would refuse is stored as `failed` with the rule, once per distinct rule."""
        try:
            validate_week(week)
        except WeekRuleError as error:
            self._record_dry_run_refusal(now, _failure_detail(error))
            return
        fingerprint = fingerprint_week(week)
        latest = self._mariadb.read_latest_comfort_schedule_dry_run_fingerprint()
        if fingerprint == latest:
            return
        self._log_kept_days(kept_days)
        logger.info("Comfort Schedule would write: %s", week)
        self._mariadb.write_heating_control_action(
            now,
            ACTION_COMFORT_SCHEDULE,
            None,
            None,
            True,
            OUTCOME_DRY_RUN,
            fingerprint,
        )

    def _record_dry_run_refusal(self, now: datetime, refusal: str) -> None:
        if refusal == self._mariadb.read_latest_comfort_schedule_dry_run_fingerprint():
            return
        logger.warning("Comfort Schedule would be refused: %s", refusal)
        self._mariadb.write_heating_control_action(
            now,
            ACTION_COMFORT_SCHEDULE,
            None,
            None,
            True,
            OUTCOME_FAILED,
            refusal,
        )

    def _pending_row(self, week: dict[str, Any]) -> ComfortScheduleAction | None:
        """The newest real row when it was sent (or not verified) with this
        week's fingerprint and is awaiting Hive showing it, else None."""
        latest = self._mariadb.read_latest_real_comfort_schedule_action()
        if (
            latest is not None
            and latest.outcome in (OUTCOME_SENT, OUTCOME_NOT_VERIFIED)
            and latest.detail == fingerprint_week(week)
        ):
            return latest
        return None

    def _write_week(
        self,
        now: datetime,
        week: dict[str, Any],
        kept_days: list[tuple[str, str]],
        pending: ComfortScheduleAction | None,
    ) -> None:
        """Send the week and store its outcome; a failed first post is stored as
        `failed` with the exception type name only, and never raised out of the
        poll. An offline answer, or a resend of a pending week (failed or not),
        stores no row, so the pending row stays newest, except that a week still
        unshown NOT_VERIFIED_AFTER after it was sent is stored once as
        `not_verified`. Kept days are logged only when a new week is posted."""
        if pending is None:
            self._log_kept_days(kept_days)
        detail = None
        try:
            outcome = self._client.write_comfort_schedule(week)
        except Exception as error:  # pylint: disable=broad-exception-caught
            outcome, detail = OUTCOME_FAILED, _failure_detail(error)
            logger.warning("Comfort Schedule write failed: %s", detail)
            if pending is not None:
                return
        if outcome == OUTCOME_OFFLINE:
            logger.debug(OFFLINE_MESSAGE)
            return
        if outcome == OUTCOME_SENT:
            detail = fingerprint_week(week)
            if pending is not None:
                if not self._is_overdue(now, pending):
                    logger.debug("Comfort Schedule week resent: Hive has not shown it")
                    return
                logger.warning(
                    "Comfort Schedule write was not verified: Hive has not shown "
                    "the week sent."
                )
                outcome = OUTCOME_NOT_VERIFIED
        self._mariadb.write_heating_control_action(
            now, ACTION_COMFORT_SCHEDULE, None, None, False, outcome, detail
        )

    @staticmethod
    def _is_overdue(now: datetime, pending: ComfortScheduleAction) -> bool:
        return (
            pending.outcome == OUTCOME_SENT
            and now.replace(tzinfo=None) - pending.acted_at >= NOT_VERIFIED_AFTER
        )


class HeatingRetriever:
    _client: HiveSource

    def __init__(  # pylint: disable=too-many-arguments
        self,
        client: HiveSource,
        mariadb: MariaDBClient | None = None,
        control: str = CONTROL_OFF,
        away_temperature: float = 10.0,
        *,
        comfort_control: str = CONTROL_OFF,
    ) -> None:
        for setting in (control, comfort_control):
            if setting not in (CONTROL_OFF, CONTROL_DRY_RUN, CONTROL_ON):
                raise ValueError(f"Unknown heating control setting: {setting!r}")
        self._client = client
        self._comfort_schedule_control = (
            ComfortScheduleControl(client, mariadb, comfort_control, away_temperature)
            if mariadb is not None and comfort_control != CONTROL_OFF
            else None
        )
        self._presence_control = (
            PresenceHeatingControl(client, mariadb, control, away_temperature)
            if mariadb is not None
            else None
        )

    def refresh(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        status = self._client.fetch_heating_status()
        self._client.persist_heating_status(status)
        # Before presence control, so a poll that restores the schedule has
        # already written the Comfort Schedule.
        if self._comfort_schedule_control is not None:
            self._comfort_schedule_control.apply(now, status)
        if self._presence_control is not None:
            self._presence_control.apply(now, status)
