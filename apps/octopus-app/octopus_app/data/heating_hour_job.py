"""Recomputes the recent completed hours of heating and what each cost."""

from datetime import UTC, datetime, timedelta

from common import local_day
from octopus_app.data import heating_hours
from octopus_app.data.model import HeatingHourRecord
from octopus_app.data.mysql.client import MariaDBClient

RECOMPUTE_LOCAL_DAYS = 4  # today and the three before it


class HeatingHourJob:
    def __init__(self, mariadb: MariaDBClient, region_code: str) -> None:
        self._mariadb = mariadb
        self._region_code = region_code

    def refresh(self, now: datetime | None = None) -> None:
        """Rewrites every hour that has ended in the last few local days. An hour
        without enough polls gets no row, and loses any row it had."""
        now = (now or datetime.now(UTC)).astimezone(UTC)
        first_day = local_day.to_local_date(now) - timedelta(
            days=RECOMPUTE_LOCAL_DAYS - 1
        )
        start = local_day.start_of_local_day(first_day).astimezone(UTC)
        start = start.replace(tzinfo=None)
        end = now.replace(minute=0, second=0, microsecond=0, tzinfo=None)

        polls = self._mariadb.read_heating_polls(start, end)
        computed_at = now.replace(tzinfo=None)
        rows = []
        for hour, (poll_count, on_minutes) in sorted(
            heating_hours.heating_on_minutes(polls).items()
        ):
            unit_rate = self._mariadb.read_gas_unit_rate(self._region_code, hour)
            kwh, cost = heating_hours.estimated_cost(on_minutes, unit_rate)
            rows.append(
                HeatingHourRecord(
                    hour_start=hour,
                    poll_count=poll_count,
                    on_minutes=on_minutes,
                    measured_kwh=None,
                    heating_kwh=kwh,
                    source=heating_hours.ESTIMATED,
                    unit_rate=unit_rate,
                    cost_gbp=cost,
                    computed_at=computed_at,
                )
            )
        self._mariadb.replace_heating_hours(start, end, rows)
