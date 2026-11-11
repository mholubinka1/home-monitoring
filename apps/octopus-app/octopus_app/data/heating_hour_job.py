"""Recomputes the recent completed hours of heating and what each cost."""

from datetime import UTC, datetime, timedelta

from common import local_day
from octopus_app.data import heating_hours
from octopus_app.data.model import Energy, HeatingHourRecord
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

        on_hours = heating_hours.heating_on_minutes(
            self._mariadb.read_heating_polls(start, end)
        )
        gas = self._measured_gas(start, end)
        baselines = self._baselines(start, end, on_hours, gas)
        computed_at = now.replace(tzinfo=None)
        rows = [
            self._record(hour, polls_on, gas.get(hour), baselines, computed_at)
            for hour, polls_on in sorted(on_hours.items())
        ]
        self._mariadb.replace_heating_hours(start, end, rows)

    def _record(
        self,
        hour: datetime,
        polls_on: tuple[int, int],
        measured: float | None,
        baselines: dict[int, float],
        computed_at: datetime,
    ) -> HeatingHourRecord:
        poll_count, on_minutes = polls_on
        unit_rate = self._mariadb.read_gas_unit_rate(self._region_code, hour)
        if measured is None:
            source = heating_hours.ESTIMATED
            kwh, cost = heating_hours.estimated_cost(on_minutes, unit_rate)
        else:
            source = heating_hours.MEASURED
            kwh = (
                heating_hours.measured_heating_kwh(
                    measured, heating_hours.baseline_for(baselines, hour)
                )
                if on_minutes
                else 0.0
            )
            cost = None if unit_rate is None else kwh * unit_rate / 100
        return HeatingHourRecord(
            hour_start=hour,
            poll_count=poll_count,
            on_minutes=on_minutes,
            measured_kwh=measured,
            heating_kwh=kwh,
            source=source,
            unit_rate=unit_rate,
            cost_gbp=cost,
            computed_at=computed_at,
        )

    def _measured_gas(self, start: datetime, end: datetime) -> dict[datetime, float]:
        readings = self._mariadb.read_half_hourly_consumption(
            Energy.gas, start.replace(tzinfo=UTC), end.replace(tzinfo=UTC)
        )
        return heating_hours.measured_gas(
            (period_from, float(kwh)) for period_from, kwh in readings
        )

    def _baselines(
        self,
        start: datetime,
        end: datetime,
        on_hours: dict[datetime, tuple[int, int]],
        gas: dict[datetime, float],
    ) -> dict[int, float]:
        """Hot-water baselines from the heating-off hours stored in the 28 days
        before the recompute window plus those of the window itself."""
        earlier = self._mariadb.read_heating_hours(
            end - timedelta(days=heating_hours.BASELINE_DAYS), start
        )
        return heating_hours.hot_water_baselines(
            [
                (row.hour_start, row.measured_kwh)
                for row in earlier
                if row.source == heating_hours.MEASURED
                and row.on_minutes == 0
                and row.measured_kwh is not None
            ]
            + [
                (hour, gas[hour])
                for hour, (_, on_minutes) in on_hours.items()
                if on_minutes == 0 and hour in gas
            ]
        )
