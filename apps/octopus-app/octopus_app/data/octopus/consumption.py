from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from octopus_app.data.model import Consumption, Energy, get_raw_unit, to_estimated_kwh
from octopus_app.data.octopus.model import Electricity, Gas, Meter
from octopus_app.data.octopus.timestamps import to_utc_z
from octopus_app.data.octopus.transport import OctopusTransport

DEFAULT_PAGE_SIZE = 5000  # a routine 45-day window is one request (ADR-0027)


class ConsumptionReading(BaseModel):
    consumption: Decimal
    interval_start: datetime
    interval_end: datetime


class ConsumptionResponse(BaseModel):
    results: list[ConsumptionReading]
    next: str | None = None


class ConsumptionClient:
    _consumption_funcs: dict

    def __init__(self, transport: OctopusTransport) -> None:
        self._transport = transport
        self._consumption_funcs: dict = {
            Energy.electricity: self.get_electricity_consumption,
            Energy.gas: self.get_gas_consumption,
        }

    def get_consumption(
        self, meter: Meter, period_from: datetime, period_to: datetime | None = None
    ) -> tuple[str | None, list[Consumption]]:
        func = self._consumption_funcs[meter.energy]
        value = func(meter, period_from, period_to)
        return value

    def get_electricity_consumption(
        self,
        meter: Electricity,
        period_from: datetime | None,
        period_to: datetime | None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[str | None, list[Consumption]]:
        api_endpoint = (
            self._transport.base_url
            + f"electricity-meter-points/{meter.mpan}/meters/{meter.serial_number}/consumption/"
        )
        params = self._build_params(period_from, period_to, page_size)
        return self.get_consumption_directly_from_endpoint(
            Energy.electricity, api_endpoint, params
        )

    def get_gas_consumption(
        self,
        meter: Gas,
        period_from: datetime | None,
        period_to: datetime | None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[str | None, list[Consumption]]:
        api_endpoint = (
            self._transport.base_url
            + f"gas-meter-points/{meter.mprn}/meters/{meter.serial_number}/consumption/"
        )
        params = self._build_params(period_from, period_to, page_size)
        return self.get_consumption_directly_from_endpoint(
            Energy.gas, api_endpoint, params
        )

    def _build_params(
        self,
        period_from: datetime | None,
        period_to: datetime | None,
        page_size: int,
    ) -> dict[str, Any]:
        # Newest first, not "period": ascending paging duplicates and skips
        # intervals (ADR-0027).
        params: dict[str, Any] = {"page_size": page_size, "order_by": "-period"}
        if period_from:
            params["period_from"] = to_utc_z(period_from)
        if period_to:
            params["period_to"] = to_utc_z(period_to)
        return params

    def get_consumption_directly_from_endpoint(
        self,
        energy: Energy,
        api_endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[str | None, list[Consumption]]:
        parsed = self._transport.get(
            api_endpoint,
            ConsumptionResponse,
            params=params,
            description="fetch consumption",
        )
        consumption = [
            Consumption(
                raw=reading.consumption,
                est_kwh=to_estimated_kwh(energy, reading.consumption),
                unit=get_raw_unit(energy),
                # Octopus returns interval_start/interval_end in local British
                # time (e.g. +01:00 during BST), not UTC -- converting here
                # keeps storage correct year-round rather than relying on
                # whatever offset happens to be in effect at fetch time.
                start=reading.interval_start.astimezone(UTC),
                end=reading.interval_end.astimezone(UTC),
            )
            for reading in parsed.results
        ]
        return (parsed.next, consumption)
