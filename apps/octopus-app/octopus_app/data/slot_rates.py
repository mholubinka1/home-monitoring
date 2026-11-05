from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from common import local_day
from octopus_app.data.octopus.model import Rate
from octopus_app.data.rate_segments import (
    RateSegment,
    day_segments,
    segments_fully_cover_day,
)

# A product publishes rates only a day or two ahead, so a slot beyond them
# repeats the pattern of the latest fully published local day: searched back
# from the last day needing a rate to this many days before the as-of day.
PATTERN_LOOKBACK_DAYS = 7


class SlotRateSource(Protocol):
    region_code: str

    def read_product_rates_for_local_day(
        self, product_code: str, region: str, day: date
    ) -> list[Rate]: ...


class NoFullyPublishedDayError(RuntimeError):
    pass


class SlotRates:
    """Unit rate of any half-hour of one product: the rate it publishes for
    it, else the same local time of day on its latest fully published day."""

    def __init__(
        self,
        client: SlotRateSource,
        as_of: datetime,
        last_day: date,
    ) -> None:
        self._client = client
        self._as_of_day = local_day.to_local_date(as_of)
        self._last_day = last_day
        self._published: dict[tuple[str, date], list[RateSegment]] = {}
        self._patterns: dict[str, tuple[date, list[RateSegment]]] = {}

    def unit_rate(self, product_code: str, slot_start: datetime, day: date) -> Decimal:
        rate = self.published_unit_rate(product_code, slot_start, day)
        if rate is not None:
            return rate
        return self.pattern_unit_rate(product_code, slot_start)

    def published_unit_rate(
        self, product_code: str, slot_start: datetime, day: date
    ) -> Decimal | None:
        return self._rate_at(self._segments(product_code, day), slot_start)

    def pattern_unit_rate(self, product_code: str, slot_start: datetime) -> Decimal:
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
                "-- cannot project its unit rate."
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
        earliest = self._as_of_day - timedelta(days=PATTERN_LOOKBACK_DAYS)
        for days_back in range((self._last_day - earliest).days + 1):
            day = self._last_day - timedelta(days=days_back)
            day_start = local_day.start_of_local_day(day)
            day_end = local_day.start_of_local_day(day + timedelta(days=1))
            segments = self._segments(product_code, day)
            if segments_fully_cover_day(segments, day_start, day_end):
                return day, segments
        raise NoFullyPublishedDayError(
            f"No fully published day found for {product_code} -- cannot "
            "project its unit rates."
        )

    @staticmethod
    def _rate_at(segments: list[RateSegment], instant: datetime) -> Decimal | None:
        for start, end, rate in segments:
            if start <= instant < end:
                return rate.unit_rate
        return None
