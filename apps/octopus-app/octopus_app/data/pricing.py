import logging.config
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from logging import Logger, getLogger
from typing import Protocol

from octopus_app.common.logging import APP_LOGGER_NAME, config
from octopus_app.data.model import Energy
from octopus_app.data.octopus.model import (
    Agreement,
    Direction,
    Meter,
    MeterSource,
    Product,
    Rate,
)
from octopus_app.data.tariff_projection import (
    TariffProjectionSource,
    write_tariff_projection,
)

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)


class PricingSource(MeterSource, TariffProjectionSource, Protocol):
    region_code: str

    def persist_agreement(self, meter: Meter, agreements: list[Agreement]) -> None: ...

    def fetch_products(self) -> list[Product]: ...

    def is_product_available_in_region(
        self, product_code: str, region: str
    ) -> bool: ...

    def persist_product(self, product: Product) -> None: ...

    def fetch_electricity_rates(
        self,
        product_code: str,
        tariff_code: str,
        period_from: datetime | None,
        period_to: datetime | None,
    ) -> list[Rate]: ...

    def fetch_gas_rates(
        self,
        product_code: str,
        tariff_code: str,
        period_from: datetime | None,
        period_to: datetime | None,
    ) -> list[Rate]: ...

    def persist_rate(
        self, product_code: str, region: str, rates: list[Rate]
    ) -> None: ...

    def fetch_electricity_tariff_code(
        self, product_code: str, region: str
    ) -> str | None: ...


class PricingRetriever:
    _client: PricingSource
    _retention_days: int

    def __init__(self, client: PricingSource, retention_days: int) -> None:
        self._client = client
        self._retention_days = retention_days

    def refresh(self, as_of: datetime | None = None) -> None:
        as_of = as_of or datetime.now(UTC)
        window_start = as_of - timedelta(days=self._retention_days)
        self._client.refresh_meters()
        self._sync_agreements()
        products = self._client.fetch_products()
        self._sync_product_catalogue(products)
        self._sync_own_product_rates(window_start)
        self._sync_comparison_rates(products, window_start, as_of)
        self._sync_tariff_projection(as_of)

    def _sync_tariff_projection(self, as_of: datetime) -> None:
        agreement = next(
            (
                agreement
                for meter, agreement in self._meter_agreement_pairs()
                if meter.energy == Energy.electricity
                and agreement.valid_from <= as_of
                and (agreement.valid_to is None or as_of < agreement.valid_to)
            ),
            None,
        )
        try:
            write_tariff_projection(self._client, agreement, as_of)
        except Exception:
            logger.warning(
                "Failed to write the Tariff Projection — skipping.", exc_info=True
            )

    def _sync_agreements(self) -> None:
        for meter in self._client.meters:
            self._client.persist_agreement(meter, meter.agreements)

    def _sync_product_catalogue(self, products: list[Product]) -> None:
        for product in products:
            if product.direction == Direction.EXPORT:
                continue
            if not self._client.is_product_available_in_region(
                product.product_code, self._client.region_code
            ):
                continue
            self._client.persist_product(product)

    def _meter_agreement_pairs(self) -> Iterator[tuple[Meter, Agreement]]:
        for meter in self._client.meters:
            for agreement in meter.agreements:
                yield meter, agreement

    def _sync_own_product_rates(self, window_start: datetime) -> None:
        for meter, agreement in self._meter_agreement_pairs():
            if agreement.has_zero_or_negative_width:
                logger.debug(
                    f"Agreement {agreement.product_code}/{agreement.tariff_code} "
                    f"has a zero or negative-width valid range "
                    f"({agreement.valid_from} to {agreement.valid_to}) — no rate "
                    "window is possible, skipping."
                )
                continue
            if agreement.valid_to is not None and agreement.valid_to <= window_start:
                logger.debug(
                    f"Agreement {agreement.product_code}/{agreement.tariff_code} "
                    f"ended ({agreement.valid_to}) at or before the retention window "
                    f"start ({window_start}) — skipping."
                )
                continue
            fetch_rates = (
                self._client.fetch_electricity_rates
                if meter.energy == Energy.electricity
                else self._client.fetch_gas_rates
            )
            try:
                rates = fetch_rates(
                    agreement.product_code,
                    agreement.tariff_code,
                    max(agreement.valid_from, window_start),
                    agreement.valid_to,
                )
                self._client.persist_rate(
                    agreement.product_code, self._client.region_code, rates
                )
            except Exception:
                logger.warning(
                    f"Failed to sync rates for own agreement "
                    f"{agreement.product_code}/{agreement.tariff_code} — skipping.",
                    exc_info=True,
                )

    def _ended_electricity_agreements(self, as_of: datetime) -> list[Agreement]:
        return [
            agreement
            for meter, agreement in self._meter_agreement_pairs()
            if meter.energy == Energy.electricity
            and not agreement.has_zero_or_negative_width
            and agreement.valid_to is not None
            and agreement.valid_to <= as_of
        ]

    def _sync_comparison_rates(
        self, products: list[Product], window_start: datetime, as_of: datetime
    ) -> None:
        # An electricity agreement that has ended no longer syncs rates past
        # its end, so its product is fetched here from that end onward (with
        # the agreement's own tariff code, leaving the rates already stored
        # for the agreement untouched) or its prices -- e.g. Agile after a
        # move to Go -- would stop being published to the dashboard. A product
        # that any other agreement still uses, or a zero-width one (which
        # never ran), stays owned: it must not be fetched under an
        # arbitrarily-picked billing method.
        ended_agreements = self._ended_electricity_agreements(as_of)
        ended_ids = {id(agreement) for agreement in ended_agreements}
        own_product_codes = {
            agreement.product_code
            for _, agreement in self._meter_agreement_pairs()
            if id(agreement) not in ended_ids
        }
        ended: dict[str, Agreement] = {}
        for agreement in sorted(ended_agreements, key=lambda a: a.valid_to or as_of):
            ended[agreement.product_code] = agreement
        for product in products:
            if product.direction == Direction.EXPORT:
                continue
            if product.product_code in own_product_codes:
                # Already synced with the agreement's actual tariff_code by
                # _sync_own_product_rates — re-fetching here would pick an
                # arbitrary billing method and risk overwriting the accurate
                # rate, since product_rate rows are keyed by product_code/
                # region/valid_from, not tariff_code.
                continue
            tariff_code: str | None
            ended_agreement = ended.get(product.product_code)
            if ended_agreement is not None and ended_agreement.valid_to is not None:
                tariff_code = ended_agreement.tariff_code
                period_from = max(window_start, ended_agreement.valid_to)
            else:
                tariff_code = self._client.fetch_electricity_tariff_code(
                    product.product_code, self._client.region_code
                )
                period_from = window_start
            if tariff_code is None:
                logger.info(
                    f"No electricity rate published for {product.product_code} "
                    f"in region {self._client.region_code} — skipping."
                )
                continue
            try:
                rates = self._client.fetch_electricity_rates(
                    product.product_code, tariff_code, period_from, None
                )
                self._client.persist_rate(
                    product.product_code, self._client.region_code, rates
                )
            except Exception:
                logger.warning(
                    f"Failed to sync comparison rates for "
                    f"{product.product_code}/{tariff_code} — skipping.",
                    exc_info=True,
                )
