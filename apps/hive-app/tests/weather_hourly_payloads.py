"""Open-Meteo hourly-response payloads shared by the weather tests."""

from datetime import UTC, datetime, timedelta
from typing import Any

HOURLY_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "surface_pressure",
    "wind_speed_10m",
    "precipitation",
    "shortwave_radiation",
    "cloud_cover",
    "sunshine_duration",
)


def hourly_payload(hours: list[str], **overrides: list) -> dict[str, Any]:
    count = len(hours)
    return {
        "hourly": {
            "time": hours,
            "temperature_2m": [15.0 + i for i in range(count)],
            "relative_humidity_2m": [70] * count,
            "surface_pressure": [1012.0] * count,
            "wind_speed_10m": [8.0] * count,
            "precipitation": [0.2] * count,
            "shortwave_radiation": [100.0 * i for i in range(count)],
            "cloud_cover": [40] * count,
            "sunshine_duration": [900.0 * i for i in range(count)],
            **overrides,
        }
    }


def recent_hours(count: int = 3) -> list[str]:
    """The last `count` hour stamps up to and including the current UTC hour,
    oldest first, in Open-Meteo's naive ISO format -- for tests that run
    through the real clock."""
    this_hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    return [
        (this_hour - timedelta(hours=offset)).strftime("%Y-%m-%dT%H:%M")
        for offset in range(count - 1, -1, -1)
    ]


def recent_hourly_payload(count: int = 3, **overrides: list) -> dict[str, Any]:
    return hourly_payload(recent_hours(count), **overrides)
