import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from logging import Logger, getLogger
from typing import Protocol

from common import local_day
from octopus_app.common.logging import APP_LOGGER_NAME
from octopus_app.data.octopus.model import Agreement
from octopus_app.data.slot_rates import (
    NoFullyPublishedDayError,
    SlotRates,
    SlotRateSource,
)

logger: Logger = getLogger(APP_LOGGER_NAME)

# How far ahead of a refresh the projection reaches.
PROJECTION_HORIZON_DAYS = 6
SLOT_LENGTH = timedelta(minutes=30)
# Agile publishes a rate for every slot it has, so it is never projected.
AGILE_PRODUCT_CODE = re.compile(r"^AGILE-(FLEX-)?[0-9]")


class TariffProjectionSource(SlotRateSource, Protocol):
    def persist_tariff_projection(
        self,
        product_code: str,
        region: str,
        slots: list[tuple[datetime, Decimal]],
        projected_at: datetime,
    ) -> None: ...


def write_tariff_projection(
    client: TariffProjectionSource, agreement: Agreement | None, as_of: datetime
) -> None:
    """Replaces the stored projection with the unpublished half-hours of the
    electricity agreement in force; none for no agreement or an Agile one."""
    product_code = agreement.product_code if agreement else None
    slots: list[tuple[datetime, Decimal]] = []
    if product_code is not None and not AGILE_PRODUCT_CODE.match(product_code):
        try:
            slots = _unpublished_slots(client, product_code, as_of)
        except NoFullyPublishedDayError:
            logger.warning(
                f"No fully published day for {product_code} -- its Tariff "
                "Projection is left empty."
            )
    client.persist_tariff_projection(
        product_code or "", client.region_code, slots, as_of
    )


def _unpublished_slots(
    client: TariffProjectionSource, product_code: str, as_of: datetime
) -> list[tuple[datetime, Decimal]]:
    horizon_end = as_of + timedelta(days=PROJECTION_HORIZON_DAYS)
    rates = SlotRates(client, as_of, local_day.to_local_date(horizon_end))
    slot_start = _next_half_hour(as_of)
    slots: list[tuple[datetime, Decimal]] = []
    while slot_start < horizon_end:
        day = local_day.to_local_date(slot_start)
        if rates.published_unit_rate(product_code, slot_start, day) is None:
            slots.append(
                (slot_start, rates.pattern_unit_rate(product_code, slot_start))
            )
        slot_start += SLOT_LENGTH
    return slots


def _next_half_hour(instant: datetime) -> datetime:
    instant = instant.astimezone(UTC)
    floor = instant.replace(minute=instant.minute // 30 * 30, second=0, microsecond=0)
    return floor if floor == instant else floor + SLOT_LENGTH
