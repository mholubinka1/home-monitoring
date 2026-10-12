from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Europe/London"
HALF_HOUR = timedelta(minutes=30)

# Module-level state: read at call time via get_local_timezone(), never bound
# at import, so set_local_timezone() takes effect for every caller.
_active_zone = ZoneInfo(DEFAULT_TIMEZONE)


def validate_timezone(name: str) -> ZoneInfo:
    # An unknown name raises ZoneInfoNotFoundError, which is a KeyError.
    try:
        return ZoneInfo(name)
    except (ValueError, OSError, KeyError) as exc:
        raise ValueError(f"Unknown timezone {name!r}") from exc


def set_local_timezone(name: str) -> None:
    # The configured zone is process-wide state, set once at app startup.
    global _active_zone  # pylint: disable=global-statement
    _active_zone = validate_timezone(name)


def get_local_timezone() -> ZoneInfo:
    return _active_zone


def to_local_date(instant: datetime) -> date:
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(_active_zone).date()


def start_of_local_day(local_date: date) -> datetime:
    return datetime(
        local_date.year, local_date.month, local_date.day, tzinfo=_active_zone
    ).astimezone(UTC)


def expected_half_hour_count(local_date: date) -> int:
    start_of_day = start_of_local_day(local_date)
    start_of_next_day = start_of_local_day(local_date + timedelta(days=1))
    duration = start_of_next_day - start_of_day
    return duration // HALF_HOUR
