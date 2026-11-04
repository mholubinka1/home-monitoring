import logging.config
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from logging import Logger, getLogger
from typing import Protocol

from common import local_day
from octopus_app.common.logging import APP_LOGGER_NAME, config
from octopus_app.data.model import DailyCostSummary, Energy
from octopus_app.data.octopus.model import Agreement, Rate
from octopus_app.data.rate_segments import (
    RateSegment,
    day_segments,
    segments_fully_cover_day,
)

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

HALF_HOUR = timedelta(minutes=30)

# A product publishes rates only a day or two ahead, so a slot beyond them
# repeats the pattern of the latest fully published local day, searched from
# this many days past the as-of day and back through the lookback below.
PUBLISHED_RATE_HORIZON_DAYS = 2
PATTERN_LOOKBACK_DAYS = 7


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


class _SlotRates:
    """Unit rate of any half-hour: the rate its covering agreement's product
    publishes for it, else the same local time of day on the latest fully
    published day of that product."""

    def __init__(
        self,
        client: TimeOfUseSource,
        agreement_for_slot: Callable[[datetime], Agreement],
        as_of: datetime,
    ) -> None:
        self._client = client
        self._agreement_for_slot = agreement_for_slot
        self._as_of_day = local_day.to_local_date(as_of)
        self._published: dict[tuple[str, date], list[RateSegment]] = {}
        self._patterns: dict[str, tuple[date, list[RateSegment]]] = {}

    def unit_rate(self, slot_start: datetime, day: date) -> Decimal:
        product_code = self._agreement_for_slot(slot_start).product_code
        rate = self._rate_at(self._segments(product_code, day), slot_start)
        if rate is not None:
            return rate
        pattern_day, pattern_segments = self._pattern(product_code)
        local_zone = local_day.get_local_timezone()
        # By wall-clock time, so a clock-change day on either side lines up
        # (a repeated autumn hour maps to its first occurrence, and a local
        # time skipped in spring resolves an hour later: only a rate change
        # inside those hours could differ, and Go has none).
        same_clock_time = datetime.combine(
            pattern_day, slot_start.astimezone(local_zone).time(), tzinfo=local_zone
        ).astimezone(UTC)
        rate = self._rate_at(pattern_segments, same_clock_time)
        if rate is None:
            raise RuntimeError(
                f"No product_rate found for {product_code} covering {slot_start} "
                "-- cannot project remaining billing period cost."
            )
        return rate

    def _segments(self, product_code: str, day: date) -> list[RateSegment]:
        key = (product_code, day)
        if key not in self._published:
            self._published[key] = day_segments(
                self._client.read_product_rates_for_local_day(
                    product_code, self._client.region_code, day
                ),
                local_day.start_of_local_day(day),
                local_day.start_of_local_day(day + timedelta(days=1)),
            )
        return self._published[key]

    def _pattern(self, product_code: str) -> tuple[date, list[RateSegment]]:
        if product_code not in self._patterns:
            self._patterns[product_code] = self._latest_full_day(product_code)
        return self._patterns[product_code]

    def _latest_full_day(self, product_code: str) -> tuple[date, list[RateSegment]]:
        first = self._as_of_day + timedelta(days=PUBLISHED_RATE_HORIZON_DAYS)
        for days_back in range(PUBLISHED_RATE_HORIZON_DAYS + PATTERN_LOOKBACK_DAYS + 1):
            day = first - timedelta(days=days_back)
            day_start = local_day.start_of_local_day(day)
            day_end = local_day.start_of_local_day(day + timedelta(days=1))
            segments = self._segments(product_code, day)
            if segments_fully_cover_day(segments, day_start, day_end):
                return day, segments
        raise RuntimeError(
            f"No fully published day found for {product_code} -- cannot "
            "project remaining billing period cost."
        )

    @staticmethod
    def _rate_at(segments: list[RateSegment], instant: datetime) -> Decimal | None:
        for start, end, rate in segments:
            if start <= instant < end:
                return rate.unit_rate
        return None


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
        self._as_of = as_of
        self._slot_rates = _SlotRates(client, agreement_for_slot, as_of)

    def variable_cost(
        self,
        usage_since: datetime,
        billing_period_end: date,
        daily_kwh: Decimal,
        real_days: list[DailyCostSummary],
    ) -> Decimal:
        shares = self._time_of_day_shares(usage_since, real_days)
        day = local_day.to_local_date(self._as_of)
        total = Decimal(0)
        while day <= billing_period_end:
            total += self._day_cost(day, daily_kwh, shares)
            day += timedelta(days=1)
        return total

    def _day_cost(
        self, day: date, daily_kwh: Decimal, shares: dict[time, Decimal]
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
                * self._slot_rates.unit_rate(slot, day)
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
        totals: dict[time, Decimal] = {}
        for instant, kwh in readings:
            local = instant.astimezone(local_zone)
            if local.date() in days:
                totals[local.time()] = totals.get(local.time(), Decimal(0)) + kwh
        return totals
