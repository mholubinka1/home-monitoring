from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from logging import Logger
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateColumn

from common.config import MariaDBSettings
from common.exceptions import MariaDBError
from common.mariadb.model import account_postcode, job_run

# account_postcode holds a single row at this fixed primary key.
_ACCOUNT_POSTCODE_ROW_ID = 1

# MySQL/MariaDB error 1050: "Table '...' already exists".
_TABLE_ALREADY_EXISTS_ERROR_CODE = 1050


def _is_table_already_exists_error(exc: OperationalError | ProgrammingError) -> bool:
    orig_args: tuple[object, ...] = getattr(exc.orig, "args", ())
    return bool(orig_args) and orig_args[0] == _TABLE_ALREADY_EXISTS_ERROR_CODE


def upsert(s: Session, record: Any, key_columns: Sequence[str] | None = None) -> None:
    """Insert the record, or update the existing row on a unique-key conflict.

    The conflict is resolved on the primary key unless key_columns names a
    different unique key (then `id` and the rest of the primary key are left
    untouched on update). An empty key_columns is a caller error, not a request
    for the primary key: pass None for that.
    """
    if key_columns is not None and len(key_columns) == 0:
        raise ValueError("upsert key_columns must name at least one column or be None.")
    try:
        with s.begin_nested():
            s.add(record)
            s.flush()
            return
    except IntegrityError as exc:
        pk_columns = [col.name for col in inspect(type(record)).primary_key]
        match_columns = list(key_columns) if key_columns is not None else pk_columns
        match_filter = {col: getattr(record, col) for col in match_columns}
        update_dict = {
            col.name: getattr(record, col.name)
            for col in record.__table__.columns
            if key_columns is None or col.name not in pk_columns
        }
        if (
            s.query(type(record))
            .filter_by(**match_filter)
            .update(update_dict, synchronize_session=False)
        ):
            return
        raise RuntimeError(
            f"Upsert conflict resolution failed: no {type(record).__name__} row "
            f"matched {match_filter}. The IntegrityError was likely caused by a "
            "constraint other than the one those columns identify."
        ) from exc


class SessionBuilder:
    session: sessionmaker
    engine: Engine

    def __init__(self, settings: MariaDBSettings):
        uri = f"mysql+pymysql://{settings.username}:{settings.password}@{settings.host}:{settings.port}/{settings.database}"
        # Every model's schema="octopus" (see mariadb/model.py) is a fixed
        # translation-map key, not a database name -- this is what actually
        # makes settings.database retarget every query. See ADR-0025.
        self.engine = create_engine(uri).execution_options(
            schema_translate_map={"octopus": settings.database}
        )
        self.session = sessionmaker(bind=self.engine)


class MariaDBClientBase:
    def __init__(
        self,
        settings: MariaDBSettings,
        # sqlalchemy-stubs (a pre-2.0 stub package -- see the repo-wide caveat
        # in apps/octopus-app/tests/test_schema_sync.py) can't express the
        # metaclass of a declarative_base()-produced class
        # precisely, so this is typed loosely rather than fought with
        # per-call-site type: ignore comments.
        declarative_base: type[Any],
        logger: Logger,
    ) -> None:
        self._declarative_base = declarative_base
        self._logger = logger
        self._session_builder = SessionBuilder(settings)
        self._sync_schema()

    def _sync_schema(self) -> None:
        engine = self._session_builder.engine
        existing_tables = set(inspect(engine).get_table_names())

        self._create_all_tolerating_concurrent_creation(engine)

        created_tables = {
            table.name for table in self._declarative_base.metadata.tables.values()
        } - existing_tables
        if created_tables:
            self._logger.info(
                f"Schema sync: created missing tables: {sorted(created_tables)}"
            )

        inspector = inspect(engine)
        # MariaDB/MySQL DDL auto-commits per statement, so this transaction
        # doesn't make the ADD COLUMN / CREATE INDEX loops atomic -- it's just
        # a connection scope. Idempotent regardless: a re-run picks up
        # anything not yet added.
        with engine.begin() as connection:
            self._sync_missing_columns(connection, inspector)
            self._sync_missing_indexes(connection, inspector)

    def _create_all_tolerating_concurrent_creation(self, engine: Engine) -> None:
        """create_all's own checkfirst existence check and the CREATE TABLE
        statement it issues aren't atomic -- if octopus-app and hive-app
        (which share octopus.job_run, see the job_run model's own comment in
        common/mariadb/model.py) both start against a freshly-
        initialized database at the same time, both can see that table as
        absent and race to create it, with the loser's CREATE TABLE failing
        "table already exists". Retrying once (checkfirst now sees the
        winner's table and skips it) recovers from exactly that race
        without weakening the check for a genuine schema problem, which
        would fail identically on the retry too.

        This lives once here in libs/common now (it used to be duplicated
        between each app's own mysql/client.py, which had to be kept in sync
        by hand) -- keep this in mind if the retry logic is ever extended,
        e.g. to tolerate another error code."""
        metadata = self._declarative_base.metadata
        try:
            metadata.create_all(engine, checkfirst=True)
        except (OperationalError, ProgrammingError) as e:
            if not _is_table_already_exists_error(e):
                raise
            self._logger.info(
                "Schema sync: table creation raced with another process "
                "(e.g. octopus-app/hive-app starting concurrently) -- retrying now "
                "that the table exists."
            )
            metadata.create_all(engine, checkfirst=True)

    def _sync_missing_columns(
        self, connection: Connection, inspector: Inspector
    ) -> None:
        for table in self._declarative_base.metadata.tables.values():
            schema = connection.schema_for_object(table)
            existing_columns = {
                column["name"]
                for column in inspector.get_columns(table.name, schema=schema)
            }
            missing_columns = [
                column
                for column in table.columns
                if column.name not in existing_columns
            ]
            if not missing_columns:
                continue

            self._logger.info(
                f"Schema sync: adding missing columns to {table.name}: "
                f"{[column.name for column in missing_columns]}"
            )

            # schema is settings.database after translation (see ADR-0025) --
            # an operator-configured value, not always the literal "octopus"
            # -- so it must be identifier-quoted like table.name already is
            # via CreateColumn's own compilation, rather than interpolated
            # raw into DDL.
            preparer = connection.dialect.identifier_preparer
            qualified_name = (
                f"{preparer.quote(schema)}.{preparer.quote(table.name)}"
                if schema
                else preparer.quote(table.name)
            )
            for column in missing_columns:
                column_ddl = CreateColumn(column).compile(dialect=connection.dialect)
                connection.execute(
                    text(f"ALTER TABLE {qualified_name} ADD COLUMN {column_ddl}")
                )

    def _sync_missing_indexes(
        self, connection: Connection, inspector: Inspector
    ) -> None:
        for table in self._declarative_base.metadata.tables.values():
            schema = connection.schema_for_object(table)
            existing_index_names = {
                index["name"]
                for index in inspector.get_indexes(table.name, schema=schema)
            }
            missing_indexes = [
                index
                for index in table.indexes
                if index.name not in existing_index_names
            ]
            if not missing_indexes:
                continue

            self._logger.info(
                f"Schema sync: creating missing indexes on {table.name}: "
                f"{[index.name for index in missing_indexes]}"
            )
            for index in missing_indexes:
                index.create(bind=connection)

    @contextmanager
    def session_read_scope(self) -> Generator[Session]:
        session = self._session_builder.session()
        try:
            yield session
        finally:
            session.close()

    @contextmanager
    def session_write_scope(self) -> Generator[Session]:
        session = self._session_builder.session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _write_all(
        self,
        records: list[Any],
        description: str,
        key_columns: Sequence[str] | None = None,
        redact_errors: bool = False,
    ) -> None:
        """Upserts every record in one transaction. A SQLAlchemy error message
        embeds the bound parameters, so with redact_errors (for records that
        carry personal data) only the exception type is logged and raised,
        with no chained cause."""
        try:
            with self.session_write_scope() as s:
                for record in records:
                    upsert(s, record, key_columns)
                self._logger.debug(f"{description}: {len(records)} written to MariaDB.")
                return
        except Exception as e:
            if redact_errors:
                error_type = type(e).__name__
                self._logger.error(f"Failed to write {description}: {error_type}")
                raise MariaDBError(error_type) from None
            self._logger.error(f"Failed to write {description}: {e}")
            raise MariaDBError(e) from e

    def write_account_postcode(self, postcode: str) -> None:
        # Not via _write_all: a SQLAlchemy error message embeds the bound
        # parameters, i.e. the household postcode. Only the exception type is
        # logged and raised, with no chained cause.
        try:
            with self.session_write_scope() as s:
                upsert(
                    s,
                    account_postcode(
                        id=_ACCOUNT_POSTCODE_ROW_ID,
                        postcode=postcode,
                        updated_at=datetime.now(UTC),
                    ),
                )
        except Exception as e:
            error_type = type(e).__name__
            self._logger.error(f"Failed to write account postcode: {error_type}")
            raise MariaDBError(error_type) from None

    def read_account_postcode(self) -> str | None:
        with self.session_read_scope() as session:
            row = (
                session.query(account_postcode)
                .filter_by(id=_ACCOUNT_POSTCODE_ROW_ID)
                .first()
            )
            return row.postcode if row is not None else None

    def has_successful_job_run(self, job_name: str) -> bool:
        with self.session_read_scope() as session:
            return (
                session.query(job_run)
                .filter_by(job_name=job_name, status="success")
                .first()
                is not None
            )

    def latest_job_run_is_successful(self, job_name: str) -> bool:
        with self.session_read_scope() as session:
            latest = (
                session.query(job_run)
                .filter_by(job_name=job_name)
                .order_by(job_run.ran_at.desc(), job_run.id.desc())
                .first()
            )
            return latest is not None and latest.status == "success"

    def record_job_run(
        self, job_name: str, status: str, error: str | None = None
    ) -> None:
        try:
            with self.session_write_scope() as s:
                record = job_run(
                    job_name=job_name,
                    status=status,
                    ran_at=datetime.now(UTC),
                    error_message=error,
                )
                s.add(record)
                self._logger.debug(f"Recorded job run: {job_name} ({status}).")
                return
        except Exception as e:
            self._logger.error(f"Failed to record job run for {job_name}: {e}")
            raise MariaDBError(e) from e
