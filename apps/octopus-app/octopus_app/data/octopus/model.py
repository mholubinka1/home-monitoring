import calendar
import logging.config
import re
from abc import ABC
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from logging import Logger, getLogger
from typing import TYPE_CHECKING, Any, Protocol

from octopus_app.common.exceptions import ArgumentError, NullValueError
from octopus_app.common.logging import APP_LOGGER_NAME, config
from octopus_app.data.model import Energy

if TYPE_CHECKING:
    from data.octopus.account import (
        AgreementInfo,
        ElectricityMeterPointInfo,
        GasMeterPointInfo,
    )

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

PRODUCT_CODE_REGEX = "^[A-Z]-[0-9A-Z]+-(?P<product_code>[A-Z0-9-]+)-[A-Z]$"


@dataclass
class Account:
    number: str
    address: str
    postcode: str


@dataclass
class BillingPeriod:
    start: date
    end: date

    @classmethod
    def from_billing_options(
        cls, period_start: date, period_end: date | None, is_fixed: bool
    ) -> "BillingPeriod":
        # Kraken's currentBillingPeriodStartDate/EndDate report the account
        # statement/ledger window, one day later on both ends than the
        # tariff charge window Octopus actually bills against -- confirmed
        # across 6 consecutive real bills. Shift the start back a day
        # before branching so both cases derive from the true tariff start.
        adjusted_start = period_start - timedelta(days=1)
        if is_fixed:
            if period_end is None:
                raise ValueError(
                    "Kraken reported isFixed: true with no "
                    "currentBillingPeriodEndDate -- contradicts its own "
                    'schema ("Null if the account is on flexible '
                    'billing"); refusing to guess a fallback date.'
                )
            return cls(start=adjusted_start, end=period_end - timedelta(days=1))
        # Flexible billing (isFixed: false) has no fixed end date from
        # Kraken -- fall back to the adjusted start + 1 calendar month, same
        # day-of-month, clamped to the month's last valid day, then back one
        # further day: this account's real cycle shape is [day X, day X-1 of
        # next month], not [day X, day X].
        return cls(
            start=adjusted_start,
            end=_add_one_month_clamped(adjusted_start) - timedelta(days=1),
        )


def _add_one_month_clamped(d: date) -> date:
    year = d.year + (d.month // 12)
    month = d.month % 12 + 1
    last_day_of_month = calendar.monthrange(year, month)[1]
    day = min(d.day, last_day_of_month)
    return date(year, month, day)


class Direction(Enum):
    IMPORT = "IMPORT"
    EXPORT = "EXPORT"


@dataclass
class Product:
    product_code: str
    display_name: str
    direction: Direction


@dataclass
class Price:
    energy: Energy
    tariff_code: str
    is_active: bool
    unit_rate: Decimal
    standing_charge: Decimal
    valid_from: datetime
    valid_to: datetime | None


@dataclass
class Rate:
    valid_from: datetime
    valid_to: datetime | None
    unit_rate: Decimal
    standing_charge: Decimal


@dataclass
class AgileForecastReading:
    period_from: datetime
    period_to: datetime
    unit_rate: Decimal


class TariffType(Enum):
    variable = (0,)
    economy7 = (1,)
    agile = (2,)
    fixed = (3,)
    prepay = (4,)


def to_tariff_type(tariff_code: str) -> TariffType:
    if tariff_code.startswith("E-2R"):
        return TariffType.economy7
    if "AGILE" in tariff_code:
        return TariffType.agile
    logger.warning(
        f"Tariff type determination logic is incomplete: {tariff_code} has been tagged as {TariffType.variable}."
    )
    return TariffType.variable


class Agreement:
    product_code: str
    tariff_code: str
    tariff_type: TariffType
    valid_from: datetime
    valid_to: datetime | None
    price_history: list[Price]

    def __init__(
        self, tariff_code: str, valid_from: datetime, valid_to: datetime | None
    ) -> None:
        self.tariff_code = tariff_code
        self.tariff_type = to_tariff_type(tariff_code)

        product_code_match = re.search(PRODUCT_CODE_REGEX, tariff_code)
        if not product_code_match:
            raise NullValueError(f"Agreements must have a product code: {tariff_code}")
        self.product_code = product_code_match.groupdict()["product_code"]
        self.valid_from = valid_from
        self.valid_to = valid_to
        self.price_history: list[Price] = []

    @classmethod
    def from_response(cls, info: "AgreementInfo") -> "Agreement":
        tariff_code = info.tariff_code.upper()
        valid_from = datetime.fromisoformat(info.valid_from)
        valid_to = None if not info.valid_to else datetime.fromisoformat(info.valid_to)
        return cls(tariff_code, valid_from, valid_to)

    @property
    def has_zero_or_negative_width(self) -> bool:
        return self.valid_to is not None and self.valid_from >= self.valid_to


class Meter(ABC):
    energy: Energy
    serial_number: str
    agreements: list[Agreement]

    def start_date(self) -> datetime:
        return min(a.valid_from for a in self.agreements)

    @staticmethod
    def _single_meter_point_serial_number(
        meter_points: list[Any], label: str
    ) -> tuple[Any, str]:
        if len(meter_points) == 0:
            raise NullValueError(f"No {label} meter points found.")
        if len(meter_points) > 1:
            raise ArgumentError(
                f"This software does not currently handle multiple {label}s."
            )
        meter_point = meter_points[0]
        if len(meter_point.meters) == 0:
            raise NullValueError("Meter Serial Number information not available.")
        if len(meter_point.meters) > 1:
            raise ArgumentError(
                f"This software does not currently handle multiple SNs per {label}."
            )
        return meter_point, meter_point.meters[0].serial_number

    @staticmethod
    def _require_agreements(agreements: list[Agreement]) -> None:
        # Fail fast on purpose: extracting consumption without agreements was
        # considered and closed as not planned (#387).
        if len(agreements) == 0:
            raise ArgumentError(
                "Meter must contain valid tariff information to extract pricing information."
            )


class Electricity(Meter):
    mpan: str

    def __init__(
        self, mpan: str, serial_number: str, agreements: list[Agreement]
    ) -> None:
        self.energy = Energy.electricity
        self.mpan = mpan
        self.serial_number = serial_number
        Meter._require_agreements(agreements)
        self.agreements = agreements

    @classmethod
    def from_response(
        cls, meter_points: list["ElectricityMeterPointInfo"]
    ) -> "Electricity":
        meter_point, serial_number = Meter._single_meter_point_serial_number(
            meter_points, "MPAN"
        )
        agreements = [
            Agreement.from_response(a) for a in (meter_point.agreements or [])
        ]
        return cls(
            mpan=meter_point.mpan, serial_number=serial_number, agreements=agreements
        )


class Gas(Meter):
    mprn: str

    def __init__(
        self, mprn: str, serial_number: str, agreements: list[Agreement]
    ) -> None:
        self.energy = Energy.gas
        self.mprn = mprn
        self.serial_number = serial_number
        Meter._require_agreements(agreements)
        self.agreements = agreements

    @classmethod
    def from_response(cls, meter_points: list["GasMeterPointInfo"]) -> "Gas":
        meter_point, serial_number = Meter._single_meter_point_serial_number(
            meter_points, "MPRN"
        )
        agreements = [
            Agreement.from_response(a) for a in (meter_point.agreements or [])
        ]
        return cls(
            mprn=meter_point.mprn, serial_number=serial_number, agreements=agreements
        )


class MeterSource(Protocol):
    meters: list[Meter]

    def refresh_meters(self) -> None: ...
