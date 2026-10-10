import argparse
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from hive_app.common.config import LocationSettings, get_settings
from hive_app.data.model import WeatherObservation
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.open_meteo_client import OpenMeteoClient

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


@dataclass(frozen=True)
class ChunkResult:
    start: date
    end: date
    rows_written: int


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

    def run(self, start: date, end: date) -> list[ChunkResult]:
        location = self._mariadb.read_weather_location()
        if location is None:
            raise RuntimeError(
                "No cached weather location: let hive-app resolve a location "
                "first, then run the backfill again."
            )
        client = OpenMeteoClient(
            LocationSettings(latitude=location.latitude, longitude=location.longitude),
            clock=self._clock,
        )

        results = []
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), end)
            try:
                observations = self._fetch_with_retries(client, chunk_start, chunk_end)
            except Exception as e:
                raise RuntimeError(
                    f"Weather backfill failed for {chunk_start} to {chunk_end} "
                    f"({_describe(e)}); chunks before it are stored. "
                    f"Repeat from {chunk_start}."
                ) from e
            self._mariadb.write_weather_observations(observations)
            results.append(ChunkResult(chunk_start, chunk_end, len(observations)))
            chunk_start = chunk_end + timedelta(days=1)
        return results

    def _fetch_with_retries(
        self, client: OpenMeteoClient, start: date, end: date
    ) -> list[WeatherObservation]:
        # Local loop rather than retry_with_exponential_backoff, which swallows
        # the final failure; the run must stop and name where to resume.
        for attempt in range(1, ATTEMPTS_PER_CHUNK + 1):
            try:
                return client.get_archive_observations(start, end)
            except Exception:
                if attempt == ATTEMPTS_PER_CHUNK:
                    raise
                self._sleep(BACKOFF_SECONDS * attempt)
        raise AssertionError("unreachable")  # pragma: no cover


def main(argv: Sequence[str] | None = None) -> int:
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

    backfill = WeatherHistoryBackfill(MariaDBClient(settings.mariadb))
    try:
        results = backfill.run(args.start, datetime.now(UTC).date())
    except Exception as e:
        print(f"Weather backfill failed: {e}", file=sys.stderr)
        return 1

    for result in results:
        print(f"{result.start} to {result.end}: {result.rows_written} hours stored")
    print(f"Total: {sum(r.rows_written for r in results)} hours stored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
