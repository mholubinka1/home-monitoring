import logging.config
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from logging import Logger, getLogger
from typing import Protocol

from octopus_app.common.logging import APP_LOGGER_NAME, config
from octopus_app.data import local_day
from octopus_app.data.consumption import ConsumptionFetchSource
from octopus_app.data.model import ConsumptionSummary, Energy
from octopus_app.data.mysql.client import MariaDBClient

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

# Deliberately wider than the ~2 years of history the Octopus API serves, so
# every day it still holds is requested; it simply returns nothing earlier.
BACKFILL_WINDOW_DAYS = 1096


class ConsumptionSummaryRetriever:
    _mariadb: MariaDBClient

    def __init__(self, mariadb: MariaDBClient) -> None:
        self._mariadb = mariadb

    def refresh(self, as_of: datetime | None = None) -> None:
        # The window is made of local days, so "today" is the local date, which
        # between 00:00 and 01:00 BST is a day ahead of the UTC date.
        if as_of is None:
            as_of = datetime.now(UTC)
        today = local_day.to_local_date(as_of)
        summaries = self._mariadb.read_consumption_summarization_window(today)
        self._mariadb.write_consumption_summary(summaries)
        logger.info(f"Consumption summary refresh: {len(summaries)} day(s) summarized.")


class ConsumptionSummaryBackfillSource(ConsumptionFetchSource, Protocol):
    def persist_consumption_summary(
        self, summaries: list[ConsumptionSummary]
    ) -> None: ...


class ConsumptionSummaryBackfill:
    _client: ConsumptionSummaryBackfillSource

    def __init__(self, client: ConsumptionSummaryBackfillSource) -> None:
        self._client = client

    def run(self, as_of: datetime | None = None) -> None:
        if as_of is None:
            as_of = datetime.now(UTC)
        # Anchored to midnight UTC of the cutoff date, not as_of's exact
        # time-of-day -- otherwise Octopus omits intervals before that time
        # on the oldest backfilled day, producing a partial daily total.
        cutoff_date = (as_of - timedelta(days=BACKFILL_WINDOW_DAYS)).date()
        period_from = datetime(
            cutoff_date.year, cutoff_date.month, cutoff_date.day, tzinfo=UTC
        )

        self._client.refresh_meters()
        totals: dict[tuple[Energy, date], Decimal] = {}
        counts: dict[tuple[Energy, date], int] = {}
        for meter in self._client.meters:
            next_page, consumption = self._client.fetch_consumption(meter, period_from)
            while True:
                for point in consumption:
                    # Local day, not point.start.date() (which is the UTC
                    # date), to match the daily job (ADR-0027).
                    key = (meter.energy, local_day.to_local_date(point.start))
                    totals[key] = totals.get(key, Decimal(0)) + point.est_kwh
                    counts[key] = counts.get(key, 0) + 1
                if next_page is None:
                    break
                next_page, consumption = self._client.fetch_consumption_page(
                    meter.energy, next_page
                )

        summaries = [
            ConsumptionSummary(
                energy=energy,
                date=day,
                total_kwh=total,
                half_hour_count=counts[energy, day],
            )
            for (energy, day), total in totals.items()
        ]
        self._client.persist_consumption_summary(summaries)
        logger.info(
            f"Yearly comparison backfill: {len(summaries)} day(s) summarized "
            f"across {len(self._client.meters)} meter(s)."
        )
