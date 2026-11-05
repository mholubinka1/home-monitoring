import logging.config
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from logging import Logger, getLogger
from typing import Protocol

from common import local_day
from octopus_app.common.logging import APP_LOGGER_NAME, config
from octopus_app.data.model import DailyCostSummary, Energy
from octopus_app.data.octopus.model import Agreement, Rate
from octopus_app.data.slot_rates import SlotRates

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

HALF_HOUR = timedelta(minutes=30)


class TimeOfUseSource(Protocol):
    region_code: str

    def read_product_rates_for_local_day(
        self, product_code: str, region: str, day: date
    ) -> list[Rate]: ...

    def read_half_hourly_consumption(
        self, energy: Energy, period_from: datetime, period_to: datetime
    ) -> list[tuple[datetime, Decimal]]: ...


def _half_hour_starts(day_start: datetime, day_end: datetime) -> list[datetime]:
    starts = []
    slot = day_start
    while slot < day_end:
        starts.append(slot)
        slot += HALF_HOUR
    return starts


def _remaining_fraction(slot_start: datetime, as_of: datetime) -> Decimal:
    slot_end = slot_start + HALF_HOUR
    if slot_end <= as_of:
        return Decimal(0)
    if slot_start >= as_of:
        return Decimal(1)
    return Decimal((slot_end - as_of).seconds) / Decimal(HALF_HOUR.seconds)


class TimeOfUseProjection:
    """Variable cost of the rest of a billing period on a time-of-use tariff
    (Octopus Go), priced one half-hour at a time: the rate of whichever
    agreement covers the half-hour -- so a tariff change inside the period is
    priced from its start -- times the usual usage at that time of day."""

    def __init__(
        self,
        client: TimeOfUseSource,
        energy: Energy,
        agreement_for_slot: Callable[[datetime], Agreement],
        as_of: datetime,
    ) -> None:
        self._client = client
        self._energy = energy
        self._agreement_for_slot = agreement_for_slot
        self._as_of = as_of

    def variable_cost(
        self,
        usage_since: datetime,
        billing_period_end: date,
        daily_kwh: Decimal,
        real_days: list[DailyCostSummary],
    ) -> Decimal:
        shares = self._time_of_day_shares(usage_since, real_days)
        slot_rates = SlotRates(self._client, self._as_of, billing_period_end)
        day = local_day.to_local_date(self._as_of)
        total = Decimal(0)
        while day <= billing_period_end:
            total += self._day_cost(day, daily_kwh, shares, slot_rates)
            day += timedelta(days=1)
        return total

    def _day_cost(
        self,
        day: date,
        daily_kwh: Decimal,
        shares: dict[time, Decimal],
        slot_rates: SlotRates,
    ) -> Decimal:
        slots = _half_hour_starts(
            local_day.start_of_local_day(day),
            local_day.start_of_local_day(day + timedelta(days=1)),
        )
        local_zone = local_day.get_local_timezone()
        weights = [
            shares.get(slot.astimezone(local_zone).time(), Decimal(0)) for slot in slots
        ]
        if not any(weights):
            weights = [Decimal(1)] * len(slots)
        total_weight = sum(weights, Decimal(0))
        return sum(
            (
                fraction
                * (daily_kwh * weight / total_weight)
                * slot_rates.unit_rate(
                    self._agreement_for_slot(slot).product_code, slot, day
                )
                for slot, weight in zip(slots, weights)
                if (fraction := _remaining_fraction(slot, self._as_of)) > 0
            ),
            Decimal(0),
        )

    def _time_of_day_shares(
        self, usage_since: datetime, real_days: list[DailyCostSummary]
    ) -> dict[time, Decimal]:
        # Usage by local time of day over the real elapsed days wholly on the
        # current tariff (since usage_since): days priced on a previous tariff
        # would show that tariff's usage pattern, and a switch part-way
        # through a day leaves that day half on the old tariff, so counting
        # starts from the first day wholly on this one. Empty when there is
        # no such day yet.
        first_whole_day = local_day.to_local_date(usage_since)
        if usage_since != local_day.start_of_local_day(first_whole_day):
            first_whole_day += timedelta(days=1)
        days = {d.date for d in real_days if d.date >= first_whole_day}
        if not days:
            logger.info(
                f"No whole elapsed day since {usage_since.date()} on this tariff "
                "yet -- projecting with even usage across the day."
            )
            return {}
        readings = self._client.read_half_hourly_consumption(
            self._energy,
            local_day.start_of_local_day(min(days)),
            local_day.start_of_local_day(max(days) + timedelta(days=1)),
        )
        local_zone = local_day.get_local_timezone()
        # Averaged per clock time over its readings, so the autumn clock-change
        # day's repeated hour (two readings per day) is not double-weighted.
        totals: dict[time, Decimal] = {}
        counts: dict[time, int] = {}
        for instant, kwh in readings:
            local = instant.astimezone(local_zone)
            if local.date() in days:
                totals[local.time()] = totals.get(local.time(), Decimal(0)) + kwh
                counts[local.time()] = counts.get(local.time(), 0) + 1
        return {clock: total / counts[clock] for clock, total in totals.items()}
