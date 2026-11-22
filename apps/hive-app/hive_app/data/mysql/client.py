import logging.config
from datetime import UTC, date, datetime
from logging import Logger, getLogger
from typing import Any, NamedTuple

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    and_,
    func,
    or_,
)
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from common.config import MariaDBSettings
from common.mariadb.client import MariaDBClientBase
from common.mariadb.model import job_run
from hive_app.common.logging import APP_LOGGER_NAME, config
from hive_app.data.model import (
    HeatingStatus,
    ResolvedLocation,
    WeatherForecastDay,
    WeatherForecastHour,
    WeatherObservation,
)
from hive_app.data.mysql import model as sql_model
from hive_app.data.mysql.model import (
    WEATHER_FORECAST_HOUR_KEY,
    WEATHER_OBSERVATION_KEY,
    SQLBase,
)

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

# weather_location holds a single row at this fixed primary key.
_WEATHER_LOCATION_ROW_ID = 1


# room_forecast_slot is OCTOPUS-APP's table. hive-app only reads it, so it is
# mirrored on a separate MetaData that hive-app's Schema Sync never sees (it
# must not create or own the table). Column shapes mirror
# apps/octopus-app/octopus_app/data/mysql/model.py by hand.
octopus_metadata = MetaData()

room_forecast_slot_table = Table(
    "room_forecast_slot",
    octopus_metadata,
    Column("day", Date, primary_key=True),
    Column("scenario", String(20), primary_key=True),
    Column("position", Integer, primary_key=True),
    Column("starts_at", DateTime, nullable=False),
    Column("ends_at", DateTime, nullable=False),
    Column("target", Float, nullable=False),
    Column("computed_at", DateTime, nullable=False),
    schema="octopus",
)

_PLAN_SCENARIO = "plan"


def _is_missing_table_error(error: Exception) -> bool:
    """MariaDB raises error 1146; SQLite (the test fixture) says "no such table"."""
    orig = getattr(error, "orig", None)
    if "no such table" in str(orig or error).lower():
        return True
    code = next(iter(getattr(orig, "args", None) or ()), None)
    return code == 1146


def _forecast_scoped_id(source: str, target_date: date) -> str:
    return f"{source}_{target_date.strftime('%Y%m%d')}"


def _json_safe(value: Any) -> Any:
    """A copy of `value` with datetimes/dates as ISO-8601 strings, so it fits a
    JSON column. Builds new containers; never mutates the caller's structure."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


class ComfortScheduleAction(NamedTuple):
    """A comfort_schedule action row: when (naive UTC, as stored), its outcome
    and its detail."""

    acted_at: datetime
    outcome: str
    detail: str | None


class MariaDBClient(MariaDBClientBase):
    def __init__(self, settings: MariaDBSettings) -> None:
        super().__init__(settings, declarative_base=SQLBase, logger=logger)

    def write_heating_status(self, status: HeatingStatus) -> None:
        # sqlalchemy-stubs models every Numeric subclass (Float included) as
        # TypeEngine[Decimal], so it reports a float/Decimal mismatch here even
        # though SQLAlchemy's real runtime Float column stores/returns a plain
        # Python float -- a known stub-accuracy gap, not a real type error.
        record = sql_model.heating_status(
            polled_at=status.polled_at,
            current_temp=status.current_temp,  # type: ignore[misc]
            target_temp=status.target_temp,  # type: ignore[misc]
            mode=status.mode,
            state=status.state,
            boost_active=status.boost_active,
            boost_ends_at=status.boost_ends_at,
            schedule=_json_safe(status.schedule),
            working=status.working,
            online=status.online,
        )
        self._write_all([record], "Heating status data")

    def write_weekly_schedule(
        self, captured_at: datetime, schedule: dict[str, Any] | None
    ) -> None:
        """Stores the weekly schedule when it differs from the newest stored
        one; does nothing for None."""
        if schedule is None:
            return
        with self.session_read_scope() as session:
            newest = (
                session.query(sql_model.heating_schedule.schedule)
                .order_by(sql_model.heating_schedule.id.desc())
                .limit(1)
                .scalar()
            )
        if newest == schedule:
            return
        record = sql_model.heating_schedule(captured_at=captured_at, schedule=schedule)
        self._write_all([record], "Heating schedule data")
        logger.info("Hive weekly schedule changed; stored a new heating_schedule row.")

    def latest_heating_polled_at(self) -> datetime | None:
        """When the newest heating_status row was polled (UTC), or None."""
        with self.session_read_scope() as session:
            latest = session.query(
                func.max(sql_model.heating_status.polled_at)
            ).scalar()
            # The database stores naive UTC; stamp it back as UTC.
            return latest.replace(tzinfo=UTC) if latest is not None else None

    def read_unreachable_spell(self, since: datetime) -> tuple[datetime, str] | None:
        """The start (UTC) of the current unreachable spell and the newest poll's
        cause ("offline", "OFF" or "offline and OFF"), or None when the newest
        poll is reachable. Unreachable is online false or mode OFF. Only polls
        since `since` (UTC) are read, so a spell older than that starts at the
        oldest poll in the window."""
        hs = sql_model.heating_status
        unreachable = or_(hs.online.is_(False), hs.mode == "OFF")
        in_window = hs.polled_at >= since.replace(tzinfo=None)
        with self.session_read_scope() as session:
            newest = (
                session.query(hs.online, hs.mode)
                .filter(in_window)
                .order_by(hs.polled_at.desc())
                .first()
            )
            if newest is None or not (newest.online is False or newest.mode == "OFF"):
                return None
            last_reachable = (
                session.query(func.max(hs.polled_at))
                .filter(in_window, ~unreachable)
                .scalar()
            )
            start_query = session.query(func.min(hs.polled_at)).filter(in_window)
            if last_reachable is not None:
                start_query = start_query.filter(hs.polled_at > last_reachable)
            start = start_query.scalar()
        offline = newest.online is False
        off = newest.mode == "OFF"
        cause = (
            "offline and OFF" if offline and off else "offline" if offline else "OFF"
        )
        return start.replace(tzinfo=UTC), cause

    def first_reachable_heating_polled_after(self, moment: datetime) -> datetime | None:
        """When the first reachable (online not false, mode not OFF) heating_status
        row after `moment` was polled (UTC), or None."""
        hs = sql_model.heating_status
        with self.session_read_scope() as session:
            first = (
                session.query(func.min(hs.polled_at))
                .filter(
                    hs.polled_at > moment.replace(tzinfo=None),
                    ~or_(hs.online.is_(False), hs.mode == "OFF"),
                )
                .scalar()
            )
            return first.replace(tzinfo=UTC) if first is not None else None

    def first_heating_polled_after(self, moment: datetime) -> datetime | None:
        """When the first heating_status row after `moment` was polled (UTC)."""
        with self.session_read_scope() as session:
            first = (
                session.query(func.min(sql_model.heating_status.polled_at))
                .filter(
                    sql_model.heating_status.polled_at > moment.replace(tzinfo=None)
                )
                .scalar()
            )
            return first.replace(tzinfo=UTC) if first is not None else None

    def read_open_presence_state(self) -> str | None:
        """The state of the open presence period, or None when there is none."""
        period = self.read_open_presence_period()
        return period[0] if period is not None else None

    def read_open_presence_period(self) -> tuple[str, datetime] | None:
        """The state and start (UTC) of the open presence period, or None."""
        with self.session_read_scope() as session:
            row = (
                session.query(
                    sql_model.presence_period.state,
                    sql_model.presence_period.started_at,
                )
                .filter(sql_model.presence_period.ended_at.is_(None))
                .order_by(sql_model.presence_period.started_at.desc())
                .first()
            )
        return (row[0], row[1].replace(tzinfo=UTC)) if row is not None else None

    def read_presence_state_before(self, started_at: datetime) -> str | None:
        """The state of the presence period that started latest before
        `started_at` (UTC), or None when there is none."""
        with self.session_read_scope() as session:
            return (
                session.query(sql_model.presence_period.state)
                .filter(
                    sql_model.presence_period.started_at
                    < started_at.replace(tzinfo=None)
                )
                .order_by(sql_model.presence_period.started_at.desc())
                .limit(1)
                .scalar()
            )

    def read_presence_period_end(self, started_at: datetime) -> datetime | None:
        """The end (UTC) of the presence period that started at `started_at`, or
        None while it is open or when there is no such period."""
        with self.session_read_scope() as session:
            ended = (
                session.query(sql_model.presence_period.ended_at)
                .filter(
                    sql_model.presence_period.started_at
                    == started_at.replace(tzinfo=None)
                )
                .order_by(sql_model.presence_period.id.desc())
                .limit(1)
                .scalar()
            )
        return ended.replace(tzinfo=UTC) if ended is not None else None

    def change_presence_state(
        self, state: str, phones_seen: int, changed_at: datetime
    ) -> None:
        """Closes the open presence period and opens one in `state`, in one
        transaction."""
        moment = changed_at.replace(tzinfo=None)
        with self.session_write_scope() as session:
            session.query(sql_model.presence_period).filter(
                sql_model.presence_period.ended_at.is_(None)
            ).update({"ended_at": moment})
            session.add(
                sql_model.presence_period(
                    state=state, started_at=moment, phones_seen=phones_seen
                )
            )

    def write_heating_control_action(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        acted_at: datetime,
        action: str,
        mode: str | None,
        target: float | None,
        dry_run: bool,
        outcome: str,
        detail: str | None = None,
    ) -> None:
        with self.session_write_scope() as session:
            session.add(
                sql_model.heating_control_action(
                    acted_at=acted_at.replace(tzinfo=None),
                    action=action,
                    mode=mode,
                    target=target,  # type: ignore[misc]
                    dry_run=dry_run,
                    outcome=outcome,
                    detail=detail,
                )
            )

    def read_away_override_in_force(self, dry_run: bool) -> bool:
        """Whether the newest away_override has no later restore_schedule that
        applied, was skipped or was a dry run, among actions with this dry_run flag.
        For real actions (dry_run False) an override counts when it applied, was
        not_verified or failed, since a failed write may have landed. For dry runs
        only a `dry_run` override counts."""
        action = sql_model.heating_control_action
        away_outcomes = (
            [sql_model.OUTCOME_DRY_RUN]
            if dry_run
            else [
                sql_model.OUTCOME_APPLIED,
                sql_model.OUTCOME_NOT_VERIFIED,
                # A write that failed may still have landed (a timeout after the
                # change), so the return must restore it; a needless restore is
                # harmless.
                sql_model.OUTCOME_FAILED,
            ]
        )
        with self.session_read_scope() as session:
            newest = (
                session.query(action.action)
                .filter(
                    action.dry_run == dry_run,
                    or_(
                        and_(
                            action.action == sql_model.ACTION_RESTORE_SCHEDULE,
                            action.outcome.in_(
                                [
                                    sql_model.OUTCOME_APPLIED,
                                    sql_model.OUTCOME_DRY_RUN,
                                    sql_model.OUTCOME_SKIPPED,
                                ]
                            ),
                        ),
                        and_(
                            action.action == sql_model.ACTION_AWAY_OVERRIDE,
                            action.outcome.in_(away_outcomes),
                        ),
                    ),
                )
                .order_by(action.id.desc())
                .limit(1)
                .scalar()
            )
        return newest == sql_model.ACTION_AWAY_OVERRIDE

    def read_plan_slots(
        self, days: list[date]
    ) -> list[tuple[date, datetime, datetime, float]]:
        """The (day, starts_at, ends_at, target) of octopus-app's plan slots for
        these days, by day then position. Its table, so a missing one reads as
        no rows."""
        slot = room_forecast_slot_table.c
        try:
            with self.session_read_scope() as session:
                rows = (
                    session.query(slot.day, slot.starts_at, slot.ends_at, slot.target)
                    .filter(slot.scenario == _PLAN_SCENARIO, slot.day.in_(days))
                    .order_by(slot.day, slot.position)
                    .all()
                )
        except (OperationalError, ProgrammingError) as error:
            if not _is_missing_table_error(error):
                raise
            return []
        return [(r.day, r.starts_at, r.ends_at, r.target) for r in rows]

    def read_latest_comfort_schedule_dry_run_fingerprint(self) -> str | None:
        """The detail (the week's fingerprint) of the newest dry-run
        comfort_schedule action, or None when there is none."""
        action = sql_model.heating_control_action
        with self.session_read_scope() as session:
            return (
                session.query(action.detail)
                .filter(
                    action.action == sql_model.ACTION_COMFORT_SCHEDULE,
                    action.dry_run.is_(True),
                )
                .order_by(action.id.desc())
                .limit(1)
                .scalar()
            )

    def read_latest_real_comfort_schedule_action(
        self,
    ) -> ComfortScheduleAction | None:
        """The newest real (not dry run) comfort_schedule action as (acted_at
        (naive UTC, as stored), outcome, detail), or None when there is none."""
        action = sql_model.heating_control_action
        with self.session_read_scope() as session:
            row = (
                session.query(action.acted_at, action.outcome, action.detail)
                .filter(
                    action.action == sql_model.ACTION_COMFORT_SCHEDULE,
                    action.dry_run.is_(False),
                )
                .order_by(action.id.desc())
                .limit(1)
                .first()
            )
            return None if row is None else ComfortScheduleAction(*row)

    def latest_failed_job_error(self, job_name: str, since: datetime) -> str | None:
        """The first non-blank line (at most 200 characters) of the error of the most
        recent failed run of the job after `since`, or None if there is none.
        One line only: a database error's later lines carry SQL and values."""
        with self.session_read_scope() as session:
            error = (
                session.query(job_run.error_message)
                .filter(
                    job_run.job_name == job_name,
                    job_run.status == "failure",
                    job_run.ran_at > since.replace(tzinfo=None),
                )
                .order_by(job_run.ran_at.desc(), job_run.id.desc())
                .limit(1)
                .scalar()
            )
            first_line = next(
                (line.strip() for line in (error or "").splitlines() if line.strip()),
                None,
            )
            return first_line[:200] if first_line else None

    def write_weather_observations(
        self, observations: list[WeatherObservation]
    ) -> None:
        # sqlalchemy-stubs models every Numeric subclass (Float included) as
        # TypeEngine[Decimal], so it reports a float/Decimal mismatch here even
        # though SQLAlchemy's real runtime Float column stores/returns a plain
        # Python float -- a known stub-accuracy gap, not a real type error.
        records = [
            sql_model.weather_observation(
                source=observation.source,
                location=observation.location,
                observed_at=observation.observed_at,
                temp=observation.temp,  # type: ignore[misc]
                humidity=observation.humidity,  # type: ignore[misc]
                pressure=observation.pressure,  # type: ignore[misc]
                wind_speed=observation.wind_speed,  # type: ignore[misc]
                precipitation=observation.precipitation,  # type: ignore[misc]
                shortwave_radiation=observation.shortwave_radiation,  # type: ignore[misc]
                cloud_cover=observation.cloud_cover,  # type: ignore[misc]
                sunshine_duration=observation.sunshine_duration,  # type: ignore[misc]
            )
            for observation in observations
        ]
        # Redacted: a SQLAlchemy error embeds the bound values, including the
        # location key (the Weather Location's rounded coordinates).
        self._write_all(
            records,
            "Weather observation data",
            key_columns=WEATHER_OBSERVATION_KEY,
            redact_errors=True,
        )

    def write_weather_forecast(self, forecast: list[WeatherForecastDay]) -> None:
        # sqlalchemy-stubs models every Numeric subclass (Float included) as
        # TypeEngine[Decimal], so it reports a float/Decimal mismatch here even
        # though SQLAlchemy's real runtime Float column stores/returns a plain
        # Python float -- a known stub-accuracy gap, not a real type error.
        records = [
            sql_model.weather_forecast(
                id=_forecast_scoped_id(day.source, day.target_date),
                source=day.source,
                target_date=day.target_date,
                max_temp=day.max_temp,  # type: ignore[misc]
                mean_temp=day.mean_temp,  # type: ignore[misc]
                min_temp=day.min_temp,  # type: ignore[misc]
                fetched_at=day.fetched_at,
            )
            for day in forecast
        ]
        self._write_all(records, "Weather forecast data")

    def write_weather_forecast_hours(self, hours: list[WeatherForecastHour]) -> None:
        # See write_weather_observations for why the float assignments need an
        # ignore, and why errors are redacted.
        records = [
            sql_model.weather_forecast_hour(
                source=hour.source,
                location=hour.location,
                forecast_at=hour.forecast_at,
                temp=hour.temp,  # type: ignore[misc]
                shortwave_radiation=hour.shortwave_radiation,  # type: ignore[misc]
                fetched_at=hour.fetched_at,
            )
            for hour in hours
        ]
        self._write_all(
            records,
            "Weather forecast hour data",
            key_columns=WEATHER_FORECAST_HOUR_KEY,
            redact_errors=True,
        )

    def read_weather_observation_hours(
        self, location: str, start: datetime, end: datetime
    ) -> list[tuple[str, datetime]]:
        """(source, observed_at in UTC) of every stored hour for the location
        key with start <= observed_at < end, whatever its source."""
        with self.session_read_scope() as session:
            rows = (
                session.query(
                    sql_model.weather_observation.source,
                    sql_model.weather_observation.observed_at,
                )
                .filter(
                    sql_model.weather_observation.location == location,
                    sql_model.weather_observation.observed_at >= start,
                    sql_model.weather_observation.observed_at < end,
                )
                .all()
            )
            # The database stores naive UTC; stamp it back as UTC.
            return [(source, observed.replace(tzinfo=UTC)) for source, observed in rows]

    def oldest_weather_observation(self, location: str) -> datetime | None:
        """The earliest stored hour (UTC) for the location key, whatever its
        source, or None if there is none."""
        with self.session_read_scope() as session:
            oldest = (
                session.query(func.min(sql_model.weather_observation.observed_at))
                .filter(sql_model.weather_observation.location == location)
                .scalar()
            )
            # The database stores naive UTC; stamp it back as UTC.
            return oldest.replace(tzinfo=UTC) if oldest is not None else None

    @staticmethod
    def _weather_location_row(session: Session) -> sql_model.weather_location | None:
        return (
            session.query(sql_model.weather_location)
            .filter_by(id=_WEATHER_LOCATION_ROW_ID)
            .first()
        )

    def write_weather_location(self, location: ResolvedLocation) -> None:
        # See write_weather_observations for why the float assignments need an
        # ignore.
        record = sql_model.weather_location(
            id=_WEATHER_LOCATION_ROW_ID,
            latitude=location.latitude,  # type: ignore[misc]
            longitude=location.longitude,  # type: ignore[misc]
            source=location.source,
            derived_from_postcode=location.derived_from_postcode,
            resolved_at=datetime.now(UTC),
        )
        # The row records the Account Postcode it was derived from, which a
        # SQLAlchemy error message would embed -- so errors are redacted.
        self._write_all([record], "Weather location", redact_errors=True)

    def read_weather_location(self) -> ResolvedLocation | None:
        with self.session_read_scope() as session:
            row = self._weather_location_row(session)
            if row is None:
                return None
            return ResolvedLocation(
                latitude=float(row.latitude),
                longitude=float(row.longitude),
                source=row.source,  # type: ignore[arg-type]
                # Same stub gap as above: a nullable String column reads as Column.
                derived_from_postcode=row.derived_from_postcode,  # type: ignore[arg-type]
            )
