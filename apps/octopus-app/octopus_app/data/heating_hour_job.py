"""Recomputes the recent completed hours of heating and what each cost."""

from datetime import UTC, date, datetime, timedelta

from common import local_day
from octopus_app.data import heating_hours
from octopus_app.data.model import Energy, HeatingHourRecord
from octopus_app.data.mysql.client import MariaDBClient

RECOMPUTE_LOCAL_DAYS = 4  # today and the three before it


class HeatingHourJob:
    def __init__(self, mariadb: MariaDBClient, region_code: str) -> None:
        self._mariadb = mariadb
        self._region_code = region_code
        self._computed_at = datetime.now(UTC).replace(tzinfo=None)

    def refresh(self, now: datetime | None = None) -> None:
        """Rewrites every hour that has ended in the last few local days. An hour
        without enough polls gets no row, and loses any row it had. When the newest
        stored hour is older than those days (an empty table, or a stop), it first
        backfills a local day at a time from there."""
        now = (now or datetime.now(UTC)).astimezone(UTC)
        end = now.replace(minute=0, second=0, microsecond=0, tzinfo=None)
        today = local_day.to_local_date(now)
        window_first_day = today - timedelta(days=RECOMPUTE_LOCAL_DAYS - 1)
        resume_from = self._resume_day()
        if resume_from is None or resume_from >= window_first_day:
            self._recompute(now, self._day_start(window_first_day), end)
            return
        # Each day learns only from the rows the days before it have written.
        day = resume_from
        while day <= today:
            day_end = min(self._day_start(day + timedelta(days=1)), end)
            self._recompute(now, self._day_start(day), day_end)
            day += timedelta(days=1)

    def _resume_day(self) -> date | None:
        """The local day a backfill starts from: the first poll's day on an empty
        table, otherwise the day after the newest stored hour's (so a job that stopped, or
        died part-way through a backfill, carries on). None with no polls."""
        latest = self._mariadb.read_latest_heating_hour()
        if latest is not None:
            # A day is written in one go, so the newest stored hour's day is done.
            return local_day.to_local_date(latest.replace(tzinfo=UTC)) + timedelta(
                days=1
            )
        first_poll = self._mariadb.read_first_heating_poll()
        if first_poll is None:
            return None
        return local_day.to_local_date(first_poll.replace(tzinfo=UTC))

    @staticmethod
    def _day_start(day: date) -> datetime:
        return local_day.start_of_local_day(day).astimezone(UTC).replace(tzinfo=None)

    def _recompute(self, now: datetime, start: datetime, end: datetime) -> None:
        on_hours = heating_hours.heating_on_minutes(
            self._mariadb.read_heating_polls(start, end)
        )
        gas = self._measured_gas(start, end)
        baselines = self._baselines(start, end, on_hours, gas)
        self._computed_at = now.replace(tzinfo=None)
        measured = [
            self._record(hour, polls_on, gas[hour], baselines)
            for hour, polls_on in sorted(on_hours.items())
            if hour in gas
        ]
        boiler_kw = self._boiler_kw(start, end, measured)
        estimated = [
            self._record(hour, polls_on, None, baselines, boiler_kw)
            for hour, polls_on in sorted(on_hours.items())
            if hour not in gas
        ]
        rows = sorted(measured + estimated, key=lambda row: row.hour_start)
        self._mariadb.replace_heating_hours(start, end, rows)

    def _record(
        self,
        hour: datetime,
        polls_on: tuple[int, int],
        measured: float | None,
        baselines: dict[int, float],
        boiler_kw: float = heating_hours.DEFAULT_BOILER_KW,
    ) -> HeatingHourRecord:
        poll_count, on_minutes = polls_on
        unit_rate = self._mariadb.read_gas_unit_rate(self._region_code, hour)
        if measured is None:
            source = heating_hours.ESTIMATED
            kwh, cost = heating_hours.estimated_cost(on_minutes, unit_rate, boiler_kw)
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
            computed_at=self._computed_at,
        )

    def _boiler_kw(
        self, start: datetime, end: datetime, measured: list[HeatingHourRecord]
    ) -> float:
        """The boiler kW learned from the measured hours stored in the 28 days
        before the recompute window plus those of the window itself."""
        earlier = self._mariadb.read_heating_hours(
            end - timedelta(days=heating_hours.BASELINE_DAYS), start
        )
        return heating_hours.learned_boiler_kw(
            (row.on_minutes, row.heating_kwh or 0.0)
            for row in earlier + measured
            if row.source == heating_hours.MEASURED
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
