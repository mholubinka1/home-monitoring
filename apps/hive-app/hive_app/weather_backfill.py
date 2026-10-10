import argparse
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from hive_app.common.config import LocationSettings, get_settings
from hive_app.data.model import WeatherObservation
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.open_meteo_client import OpenMeteoClient
from hive_app.data.weather_types import location_key

# Days and months are local days and months; issue #663 makes this configurable.
LOCAL_TIMEZONE = ZoneInfo("Europe/London")
ARCHIVE_SOURCE = "open-meteo-archive"
ONE_HOUR = timedelta(hours=1)

# The first daily gas day: weather before it has nothing to be compared with.
DEFAULT_START = date(2024, 7, 24)
CHUNK_DAYS = 365
ATTEMPTS_PER_CHUNK = 3
BACKOFF_SECONDS = 2.0


def _describe(error: Exception) -> str:
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
    built only from dates, counts and _describe, never from an error's text."""


@dataclass(frozen=True)
class ChunkResult:
    start: date
    end: date
    rows_written: int


@dataclass(frozen=True)
class BackfillResult:
    location_key: str
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


def _local_day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=LOCAL_TIMEZONE).astimezone(UTC)


def _hour_stamps(day: date) -> list[datetime]:
    # A stamp H holds the hour ending at H (ADR-0029), so a local day's stamps
    # run from its 01:00 to the next 00:00: 23, 24 or 25 on a clock-change day.
    stamp = _local_day_start(day) + ONE_HOUR
    last = _local_day_start(day + timedelta(days=1))
    stamps = []
    while stamp <= last:
        stamps.append(stamp)
        stamp += ONE_HOUR
    return stamps


def _local_hour_start(stamp: datetime) -> datetime:
    """The local start of the hour a stamp holds; its date is the stamp's day."""
    return (stamp - ONE_HOUR).astimezone(LOCAL_TIMEZONE)


def completeness_report(
    mariadb: MariaDBClient, location: str, start: date, end: date
) -> CompletenessReport:
    """Hourly readings per local day from `start` to `end` inclusive. An hour
    counts once whichever source holds it (ADR-0010: bucketed by local date)."""
    hours = mariadb.read_weather_observation_hours(
        location,
        _local_day_start(start) + ONE_HOUR,
        _local_day_start(end + timedelta(days=1)) + ONE_HOUR,
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
            for stamp in _hour_stamps(day)
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
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._mariadb = mariadb
        self._clock = clock
        self._sleep = sleep

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

        results: list[ChunkResult] = []
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), end)
            try:
                observations = self._fetch_with_retries(client, chunk_start, chunk_end)
                self._mariadb.write_weather_observations(observations)
            except Exception as e:
                stored = (
                    f"{len(results)} earlier chunk(s) stored"
                    if results
                    else "nothing stored"
                )
                raise BackfillError(
                    f"Weather backfill failed for {chunk_start} to {chunk_end} "
                    f"({_describe(e)}); {stored}. Repeat from {chunk_start}."
                ) from e
            results.append(ChunkResult(chunk_start, chunk_end, len(observations)))
            chunk_start = chunk_end + timedelta(days=1)
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

    clock = clock or (lambda: datetime.now(UTC))
    now = clock()
    yesterday = now.astimezone(LOCAL_TIMEZONE).date() - timedelta(days=1)
    if args.start > yesterday:
        print(
            f"--start {args.start} is after yesterday ({yesterday}): "
            "pass a start on or before yesterday.",
            file=sys.stderr,
        )
        return 1

    try:
        # get_settings reports its own failures and calls sys.exit(1).
        settings = get_settings(config_file_path=args.config_file)
    except SystemExit:
        if args.config_file is None:
            print("Could not load config: pass --config-file <path>.", file=sys.stderr)
        else:
            print(f"Could not load config from {args.config_file}.", file=sys.stderr)
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
        reason = str(e) if isinstance(e, BackfillError) else _describe(e)
        print(f"Weather backfill failed: {reason}", file=sys.stderr)
        return 1

    for chunk in result.chunks:
        print(f"{chunk.start} to {chunk.end}: {chunk.rows_written} hours stored")
    print(f"Total: {sum(c.rows_written for c in result.chunks)} hours stored")

    # Today is still in progress, so the report stops at yesterday.
    try:
        report = completeness_report(
            mariadb, result.location_key, args.start, yesterday
        )
    except Exception as e:
        print(f"Weather backfill report failed: {_describe(e)}", file=sys.stderr)
        return 1
    _print_report(report, yesterday)
    return 0


if __name__ == "__main__":
    sys.exit(main())
