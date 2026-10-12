import argparse
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from logging import getLogger

import requests

from common.local_day import (
    get_local_timezone,
    hour_end_stamps,
    set_local_timezone,
    start_of_local_day,
    to_local_date,
)
from hive_app.common.config import LocationSettings, get_settings
from hive_app.common.logging import APP_LOGGER_NAME
from hive_app.data.model import WeatherObservation
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.open_meteo_client import ARCHIVE_SOURCE, OpenMeteoClient
from hive_app.data.weather_types import location_key

logger = getLogger(APP_LOGGER_NAME)

ONE_HOUR = timedelta(hours=1)

# The first daily gas day: weather before it has nothing to be compared with.
DEFAULT_START = date(2024, 7, 24)
CHUNK_DAYS = 365
ATTEMPTS_PER_CHUNK = 3
BACKOFF_SECONDS = 2.0


def describe_error(error: Exception) -> str:
    # The error type and any HTTP status, never str(error): a requests error's
    # text carries the request URL and so the location's coordinates.
    status = getattr(getattr(error, "response", None), "status_code", None)
    name = type(error).__name__
    return f"{name}, HTTP {status}" if status is not None else name


def _is_transient(error: Exception) -> bool:
    # Worth repeating: a network failure, a server error or rate limiting.
    # A rejected request or an unreadable payload will fail the same way again.
    if isinstance(
        error,
        requests.ConnectionError
        | requests.Timeout
        | requests.exceptions.ChunkedEncodingError,
    ):
        return True
    if isinstance(error, requests.HTTPError):
        status = error.response.status_code if error.response is not None else None
        return status is not None and (status == 429 or status >= 500)
    return False


class BackfillError(RuntimeError):
    """The backfill's own failure, worded for the operator and safe to print:
    built only from dates, counts and describe_error, never from an error's text."""


def printable_reason(error: Exception) -> str:
    """What may be shown for an error: a BackfillError's own text (built from
    dates, counts and describe_error), otherwise only its description."""
    return str(error) if isinstance(error, BackfillError) else describe_error(error)


@dataclass(frozen=True)
class ChunkResult:
    start: date
    end: date
    rows_written: int


@dataclass(frozen=True)
class BackfillResult:
    location_key: str
    # In the order fetched: newest first.
    chunks: list[ChunkResult]


@dataclass(frozen=True)
class CompletenessReport:
    archive_rows_per_month: dict[str, int]
    complete_days: int
    # Each incomplete day's missing hours, as local "HH:MM" hour starts.
    incomplete_days: dict[date, list[str]]

    @property
    def total_days(self) -> int:
        return self.complete_days + len(self.incomplete_days)


def _local_hour_start(stamp: datetime) -> datetime:
    """The local start of the hour a stamp holds; its date is the stamp's day."""
    return (stamp - ONE_HOUR).astimezone(get_local_timezone())


def completeness_report(
    mariadb: MariaDBClient, location: str, start: date, end: date
) -> CompletenessReport:
    """Hourly readings per local day from `start` to `end` inclusive. An hour
    counts once whichever source holds it (ADR-0010: bucketed by local date)."""
    hours = mariadb.read_weather_observation_hours(
        location,
        start_of_local_day(start) + ONE_HOUR,
        start_of_local_day(end + timedelta(days=1)) + ONE_HOUR,
    )
    present = {observed_at for _, observed_at in hours}
    archive_rows_per_month: Counter[str] = Counter(
        _local_hour_start(observed_at).strftime("%Y-%m")
        for source, observed_at in hours
        if source == ARCHIVE_SOURCE
    )

    incomplete_days = {}
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        missing = [
            _local_hour_start(stamp).strftime("%H:%M")
            for stamp in hour_end_stamps(day)
            if stamp not in present
        ]
        if missing:
            incomplete_days[day] = missing
    return CompletenessReport(
        archive_rows_per_month=dict(sorted(archive_rows_per_month.items())),
        complete_days=(end - start).days + 1 - len(incomplete_days),
        incomplete_days=incomplete_days,
    )


class WeatherHistoryBackfill:
    """Backfills hourly weather from the Open-Meteo archive for the cached
    Weather Location. Never geocodes: hive-app resolves the location."""

    def __init__(
        self,
        mariadb: MariaDBClient,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._mariadb = mariadb
        self._clock = clock
        # Looked up at construction, not import, so tests can patch time.sleep.
        self._sleep = sleep if sleep is not None else time.sleep

    def run(self, start: date, end: date) -> BackfillResult:
        location = self._mariadb.read_weather_location()
        if location is None:
            raise BackfillError(
                "No cached weather location: let hive-app resolve a location "
                "first, then run the backfill again."
            )
        client = OpenMeteoClient(
            LocationSettings(latitude=location.latitude, longitude=location.longitude),
            clock=self._clock,
        )

        # Newest chunk first: if a chunk keeps failing, the oldest stretch is the
        # one left missing, so the startup gate (how far back the stored history
        # reaches) sees the gap and retries at the next start.
        results: list[ChunkResult] = []
        chunk_end = end
        while chunk_end >= start:
            chunk_start = max(chunk_end - timedelta(days=CHUNK_DAYS - 1), start)
            try:
                observations = self._fetch_with_retries(client, chunk_start, chunk_end)
                self._mariadb.write_weather_observations(observations)
            except Exception as e:
                stored = (
                    f"{len(results)} newer chunk(s) stored"
                    if results
                    else "nothing stored"
                )
                raise BackfillError(
                    f"Weather backfill failed for {chunk_start} to {chunk_end} "
                    # Repeat from the run's start, not the failed chunk's: chunks
                    # older than it were never fetched (newest first).
                    f"({describe_error(e)}); {stored}. Repeat from {start}."
                ) from e
            results.append(ChunkResult(chunk_start, chunk_end, len(observations)))
            chunk_end = chunk_start - timedelta(days=1)
        return BackfillResult(
            location_key(location.latitude, location.longitude), results
        )

    def _fetch_with_retries(
        self, client: OpenMeteoClient, start: date, end: date
    ) -> list[WeatherObservation]:
        # Local loop rather than retry_with_exponential_backoff, which swallows
        # the final failure; the run must stop and name where to resume.
        for attempt in range(1, ATTEMPTS_PER_CHUNK + 1):
            try:
                return client.get_archive_observations(start, end)
            except Exception as e:
                if attempt == ATTEMPTS_PER_CHUNK or not _is_transient(e):
                    raise
                self._sleep(BACKOFF_SECONDS * attempt)
        raise AssertionError("unreachable")  # pragma: no cover


def _print_report(report: CompletenessReport, up_to: date) -> None:
    print(f"Completeness up to {up_to} (today is still in progress):")
    for month, rows in report.archive_rows_per_month.items():
        print(f"{month}: {rows} archive hours stored")
    print(f"complete days: {report.complete_days} of {report.total_days}")
    if not report.incomplete_days:
        print("no incomplete days")
    for day, missing in report.incomplete_days.items():
        print(f"incomplete: {day}, {len(missing)} hours missing ({', '.join(missing)})")


def _local_yesterday(now: datetime) -> date:
    """The last complete local day, where every completeness report ends."""
    return to_local_date(now) - timedelta(days=1)


def history_start() -> datetime:
    """The first hour-ending stamp of DEFAULT_START: stored weather reaching back
    this far holds the whole history the backfill would fetch."""
    return hour_end_stamps(DEFAULT_START)[0]


def backfill_and_log(mariadb: MariaDBClient, clock: Callable[[], datetime]) -> None:
    """The startup backfill, run in a background job: DEFAULT_START to today,
    then a log of the hours stored and the completeness up to yesterday. Only a
    BackfillError from the backfill itself escapes, with no cause attached: the job
    records and logs the error's text and traceback, and a raw error may carry
    coordinates or SQL. A failing report is logged as a warning, not raised."""
    now = clock()
    try:
        result = WeatherHistoryBackfill(mariadb, clock=clock).run(
            DEFAULT_START, now.date()
        )
    except Exception as e:
        # from None: a BackfillError's cause is the raw error, whose traceback
        # the job's retry logging would print.
        reason = printable_reason(e)
        raise BackfillError(reason) from None
    stored = sum(c.rows_written for c in result.chunks)
    yesterday = _local_yesterday(now)
    try:
        report = completeness_report(
            mariadb, result.location_key, DEFAULT_START, yesterday
        )
    except Exception as e:
        # The history is stored: a failed report must not fail the job, whose
        # retries would download it all again.
        logger.warning(
            f"Weather backfill stored {stored} hours; "
            f"completeness report unavailable ({describe_error(e)})."
        )
        return
    logger.info(
        f"Weather backfill stored {stored} hours; "
        f"complete days: {report.complete_days} of {report.total_days}."
    )


def main(
    argv: Sequence[str] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> int:
    """Backfill hourly weather history: run via `docker exec hive-app python -m
    hive_app.weather_backfill --config-file <path>`. Safe to repeat over the
    same range; rows are replaced, not duplicated."""
    parser = argparse.ArgumentParser(description="Backfill hourly weather history.")
    parser.add_argument("--config-file")
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        default=DEFAULT_START,
        help="First day to backfill, YYYY-MM-DD (default: %(default)s).",
    )
    args = parser.parse_args(argv)

    try:
        # get_settings reports its own failures and calls sys.exit(1).
        settings = get_settings(config_file_path=args.config_file)
    except SystemExit:
        if args.config_file is None:
            print("Could not load config: pass --config-file <path>.", file=sys.stderr)
        else:
            print(f"Could not load config from {args.config_file}.", file=sys.stderr)
        return 1
    # Before yesterday is worked out: a hand run uses the app's configured zone.
    set_local_timezone(settings.timezone)

    clock = clock or (lambda: datetime.now(UTC))
    now = clock()
    yesterday = _local_yesterday(now)
    if args.start > yesterday:
        print(
            f"--start {args.start} is after yesterday ({yesterday}): "
            "pass a start on or before yesterday.",
            file=sys.stderr,
        )
        return 1

    try:
        # Building the client connects to the database (Schema Sync).
        mariadb = MariaDBClient(settings.mariadb)
        # The archive request is in UTC (timezone=UTC), so its end is the UTC
        # date; that always covers the stamps of the local yesterday the report
        # ends on, including its closing 00:00 stamp.
        result = WeatherHistoryBackfill(mariadb, clock=clock).run(
            args.start, now.date()
        )
    except Exception as e:
        # Only the backfill's own errors are safe to print; anything else (a raw
        # database error, even a library RuntimeError) may carry SQL and values.
        reason = printable_reason(e)
        print(f"Weather backfill failed: {reason}", file=sys.stderr)
        return 1

    for chunk in sorted(result.chunks, key=lambda c: c.start):
        print(f"{chunk.start} to {chunk.end}: {chunk.rows_written} hours stored")
    print(f"Total: {sum(c.rows_written for c in result.chunks)} hours stored")

    # Today is still in progress, so the report stops at yesterday.
    try:
        report = completeness_report(
            mariadb, result.location_key, args.start, yesterday
        )
    except Exception as e:
        print(f"Weather backfill report failed: {describe_error(e)}", file=sys.stderr)
        return 1
    _print_report(report, yesterday)
    return 0


if __name__ == "__main__":
    sys.exit(main())
