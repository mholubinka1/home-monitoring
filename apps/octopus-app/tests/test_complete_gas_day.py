from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from octopus_app.data import local_day
from octopus_app.data.consumption_summary import ConsumptionSummaryRetriever
from octopus_app.data.gas_day import GasDayStatus, gas_day_status
from octopus_app.data.model import Consumption, ConsumptionSummary, Energy, Unit
from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.octopus.model import Agreement, Gas


def _gas_meter() -> Gas:
    return Gas(
        mprn="1234567890",
        serial_number="00B1234567",
        agreements=[
            Agreement(
                tariff_code="G-1R-VAR-22-11-01-A",
                valid_from=datetime(2022, 11, 1, tzinfo=UTC),
                valid_to=None,
            )
        ],
    )


def _half_hours(day: date, count: int, est_kwh: Decimal) -> list[Consumption]:
    start_of_day = local_day.start_of_local_day(day)
    return [
        Consumption(
            raw=est_kwh,
            est_kwh=est_kwh,
            unit=Unit.kwh,
            start=start_of_day + i * local_day.HALF_HOUR,
            end=start_of_day + (i + 1) * local_day.HALF_HOUR,
        )
        for i in range(count)
    ]


def _refreshed_status(
    mariadb_client: MariaDBClient, day: date, count: int, est_kwh: Decimal
) -> GasDayStatus:
    mariadb_client.write_consumption(_gas_meter(), _half_hours(day, count, est_kwh))

    ConsumptionSummaryRetriever(mariadb_client).refresh(
        as_of=datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)
    )

    [summary] = mariadb_client.read_daily_consumption_summary(Energy.gas, day, day)
    return gas_day_status(summary)


def test_a_gas_day_with_every_half_hour_is_stored_with_its_count_and_is_complete(
    mariadb_client: MariaDBClient,
) -> None:
    day = date(2026, 1, 10)
    mariadb_client.write_consumption(_gas_meter(), _half_hours(day, 48, Decimal("0.5")))

    ConsumptionSummaryRetriever(mariadb_client).refresh(
        as_of=datetime(2026, 1, 11, 12, tzinfo=UTC)
    )

    [summary] = mariadb_client.read_daily_consumption_summary(Energy.gas, day, day)
    assert summary.total_kwh == Decimal("24.00000")
    assert summary.half_hour_count == 48
    assert gas_day_status(summary) is GasDayStatus.COMPLETE


def test_a_gas_day_with_only_36_of_48_half_hours_is_incomplete(
    mariadb_client: MariaDBClient,
) -> None:
    status = _refreshed_status(mariadb_client, date(2026, 1, 10), 36, Decimal("0.5"))

    assert status is GasDayStatus.INCOMPLETE


def test_a_spring_forward_day_with_46_readings_is_complete(
    mariadb_client: MariaDBClient,
) -> None:
    status = _refreshed_status(mariadb_client, date(2026, 3, 29), 46, Decimal("0.5"))

    assert status is GasDayStatus.COMPLETE


def test_an_autumn_day_with_50_readings_is_complete(
    mariadb_client: MariaDBClient,
) -> None:
    status = _refreshed_status(mariadb_client, date(2026, 10, 25), 50, Decimal("0.5"))

    assert status is GasDayStatus.COMPLETE


def test_a_gas_day_with_48_readings_that_are_all_zero_is_incomplete(
    mariadb_client: MariaDBClient,
) -> None:
    status = _refreshed_status(mariadb_client, date(2026, 1, 10), 48, Decimal(0))

    assert status is GasDayStatus.INCOMPLETE


def test_a_gas_day_with_an_unknown_count_and_positive_total_is_unverified() -> None:
    summary = ConsumptionSummary(
        energy=Energy.gas,
        date=date(2026, 1, 10),
        total_kwh=Decimal("12.5"),
        half_hour_count=None,
    )

    assert gas_day_status(summary) is GasDayStatus.UNVERIFIED


def test_a_gas_day_with_an_unknown_count_and_zero_total_is_incomplete() -> None:
    summary = ConsumptionSummary(
        energy=Energy.gas,
        date=date(2026, 1, 10),
        total_kwh=Decimal(0),
        half_hour_count=None,
    )

    assert gas_day_status(summary) is GasDayStatus.INCOMPLETE


def test_a_gas_day_whose_gas_arrives_late_is_incomplete_until_the_next_summary_run(
    mariadb_client: MariaDBClient,
) -> None:
    day = date(2026, 1, 10)
    summarizer = ConsumptionSummaryRetriever(mariadb_client)
    mariadb_client.write_consumption(_gas_meter(), _half_hours(day, 36, Decimal("0.5")))

    summarizer.refresh(as_of=datetime(2026, 1, 11, 12, tzinfo=UTC))
    [first] = mariadb_client.read_daily_consumption_summary(Energy.gas, day, day)

    mariadb_client.write_consumption(_gas_meter(), _half_hours(day, 48, Decimal("0.5")))
    summarizer.refresh(as_of=datetime(2026, 1, 12, 12, tzinfo=UTC))
    [second] = mariadb_client.read_daily_consumption_summary(Energy.gas, day, day)

    assert gas_day_status(first) is GasDayStatus.INCOMPLETE
    assert gas_day_status(second) is GasDayStatus.COMPLETE
    assert second.total_kwh == Decimal("24.00000")
