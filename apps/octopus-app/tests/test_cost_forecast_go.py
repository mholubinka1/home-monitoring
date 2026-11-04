from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import responses
from tests.test_cost_forecast_retriever import (
    AGILE_PRODUCT_CODE,
    REGION,
    _make_electricity_meter,
    _mock_billing_period,
    _seed_complete_day,
    _source,
)

from common.local_day import start_of_local_day
from octopus_app.data.cost_forecast import CostForecastRetriever
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.octopus.model import Agreement, TariffType, to_tariff_type

GO_PRODUCT_CODE = "GO-VAR-22-10-14"
GO_TARIFF_CODE = f"E-1R-{GO_PRODUCT_CODE}-{REGION}"
PEAK_RATE = Decimal("30.00")
OFF_PEAK_RATE = Decimal("10.00")
STANDING_CHARGE = Decimal("50.00")


def test_go_tariff_code_is_detected_as_go() -> None:
    assert to_tariff_type(GO_TARIFF_CODE) == TariffType.go


@pytest.mark.parametrize(
    "tariff_code, expected",
    [
        ("E-1R-AGILE-24-10-01-H", TariffType.agile),
        ("E-2R-VAR-22-11-01-H", TariffType.economy7),
        ("E-1R-VAR-22-11-01-H", TariffType.variable),
    ],
)
def test_other_tariff_codes_keep_their_type(
    tariff_code: str, expected: TariffType
) -> None:
    assert to_tariff_type(tariff_code) == expected


def _seed_go_agreement_and_july_6_rates(s) -> None:
    s.add(
        model.agreement(
            id="E20220101000000",
            energy="E",
            product_code=GO_PRODUCT_CODE,
            tariff_code=GO_TARIFF_CODE,
            valid_from=datetime(2022, 1, 1, tzinfo=UTC),
            valid_to=None,
        )
    )
    # Local (BST) 6 July is UTC 23:00 on the 5th to 23:00 on the 6th. An
    # illustrative cheap window of 00:30-05:30 UTC; the rest of the day is peak.
    windows = [
        (
            datetime(2026, 7, 5, 23, 0, tzinfo=UTC),
            datetime(2026, 7, 6, 0, 30, tzinfo=UTC),
            PEAK_RATE,
        ),
        (
            datetime(2026, 7, 6, 0, 30, tzinfo=UTC),
            datetime(2026, 7, 6, 5, 30, tzinfo=UTC),
            OFF_PEAK_RATE,
        ),
        (
            datetime(2026, 7, 6, 5, 30, tzinfo=UTC),
            datetime(2026, 7, 7, 0, 30, tzinfo=UTC),
            PEAK_RATE,
        ),
    ]
    for valid_from, valid_to, unit_rate in windows:
        s.add(
            model.product_rate(
                id=f"{GO_PRODUCT_CODE}_{REGION}_{valid_from:%Y%m%d%H%M}",
                product_code=GO_PRODUCT_CODE,
                region=REGION,
                valid_from=valid_from,
                valid_to=valid_to,
                unit_rate=unit_rate,
                standing_charge=STANDING_CHARGE,
            )
        )


@responses.activate
def test_go_projection_uses_the_effective_rate_paid_on_elapsed_days(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-08-07")
    with mariadb_client.session_write_scope() as s:
        _seed_go_agreement_and_july_6_rates(s)
        # 0.1 kWh in each of the 38 peak slots, 0.4 kWh in each of the 10
        # off-peak slots: 7.8 kWh costing 154p in unit charges, an effective
        # 19.74p/kWh -- well below the 25.83p a flat time-weighted average
        # of the two windows would charge.
        _seed_complete_day(s, date(2026, 7, 6), "0.1")
        for row in s.query(model.consumption).all():
            if (
                datetime(2026, 7, 6, 0, 30)
                <= row.period_from
                < datetime(2026, 7, 6, 5, 30)
            ):
                row.est_kwh = row.raw_value = Decimal("0.4")

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter(tariff_code=GO_TARIFF_CODE)])
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # actual: 154p unit charges + 50p standing = £2.04
    assert row.actual_cost_to_date == Decimal("2.04")
    # 31 remaining days each repeat the elapsed day's 154p + 50p standing.
    assert row.projected_total_cost == pytest.approx(
        Decimal("2.04") + Decimal(31) * Decimal("2.04"), abs=Decimal("0.01")
    )


@responses.activate
def test_go_projection_falls_back_to_the_published_day_average_without_elapsed_days(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-07-08")
    with mariadb_client.session_write_scope() as s:
        _seed_go_agreement_and_july_6_rates(s)
        s.add(
            model.consumption(
                id="E20260706000000",
                energy="E",
                period_from=datetime(2026, 7, 6, 0, 0, tzinfo=UTC),
                period_to=datetime(2026, 7, 6, 0, 30, tzinfo=UTC),
                raw_value=Decimal("24.0"),
                unit="kWh",
                est_kwh=Decimal("24.0"),
            )
        )

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter(tariff_code=GO_TARIFF_CODE)])
    )
    # 13:00 local on 6 July: the only consumption is today's, which is
    # excluded as partial, so no real elapsed day exists to learn a rate from.
    retriever.refresh(as_of=datetime(2026, 7, 6, 12, 0, tzinfo=UTC))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # actual: 24 kWh @ 30p + 50p standing = £7.70
    assert row.actual_cost_to_date == Decimal("7.70")
    # Day average rate = (1.5h*30 + 5h*10 + 17.5h*30) / 24 = 25.8333p.
    # 35 remaining hours at 24 kWh/day: 35 * 25.8333 = 904.17p, plus one
    # remaining day's 50p standing charge.
    expected_remaining = (Decimal("904.1667") + STANDING_CHARGE) / 100
    assert row.projected_total_cost == pytest.approx(
        Decimal("7.70") + expected_remaining, abs=Decimal("0.01")
    )


@responses.activate
def test_go_projection_ignores_elapsed_days_priced_on_the_previous_tariff(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-02", "2026-08-02")
    switch_to_go = datetime(2026, 7, 1, 23, 0, tzinfo=UTC)
    agile_agreement = Agreement(
        tariff_code=f"E-1R-{AGILE_PRODUCT_CODE}-{REGION}",
        valid_from=datetime(2022, 1, 1, tzinfo=UTC),
        valid_to=switch_to_go,
    )
    with mariadb_client.session_write_scope() as s:
        for agreement_id, product_code, valid_from, valid_to in (
            ("E20220101000000", AGILE_PRODUCT_CODE, datetime(2022, 1, 1), switch_to_go),
            ("E20260701230000", GO_PRODUCT_CODE, switch_to_go, None),
        ):
            s.add(
                model.agreement(
                    id=agreement_id,
                    energy="E",
                    product_code=product_code,
                    tariff_code=f"E-1R-{product_code}-{REGION}",
                    valid_from=valid_from,
                    valid_to=valid_to,
                )
            )
        for product_code, unit_rate in (
            (AGILE_PRODUCT_CODE, "50.00"),
            (GO_PRODUCT_CODE, "20.00"),
        ):
            s.add(
                model.product_rate(
                    id=f"{product_code}_{REGION}_202601010000",
                    product_code=product_code,
                    region=REGION,
                    valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                    valid_to=None,
                    unit_rate=Decimal(unit_rate),
                    standing_charge=STANDING_CHARGE,
                )
            )
        # 4.8 kWh on 1 July (Agile, 50p) and again on 2 July (Go, 20p).
        _seed_complete_day(s, date(2026, 7, 1), "0.1")
        _seed_complete_day(s, date(2026, 7, 2), "0.1")

    retriever = CostForecastRetriever(
        _source(
            mariadb_client,
            [
                _make_electricity_meter(
                    tariff_code=GO_TARIFF_CODE,
                    valid_from=switch_to_go,
                    prior_agreements=[agile_agreement],
                )
            ],
        )
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 3)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # actual: (4.8 * 50 + 50) + (4.8 * 20 + 50) = 290p + 146p = £4.36
    assert row.actual_cost_to_date == Decimal("4.36")
    # 30 remaining days at 4.8 kWh and the Go-only 20p rate (a blend with the
    # Agile day would give 35p): 30 * (96p + 50p standing) = £43.80.
    assert row.projected_total_cost == pytest.approx(
        Decimal("4.36") + Decimal("43.80"), abs=Decimal("0.01")
    )


@responses.activate
def test_go_projection_excludes_the_day_the_account_switched_part_way_through(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-02", "2026-08-02")
    switch_to_go = datetime(2026, 7, 2, 11, 0, tzinfo=UTC)
    agile_agreement = Agreement(
        tariff_code=f"E-1R-{AGILE_PRODUCT_CODE}-{REGION}",
        valid_from=datetime(2022, 1, 1, tzinfo=UTC),
        valid_to=switch_to_go,
    )
    with mariadb_client.session_write_scope() as s:
        for agreement_id, product_code, valid_from, valid_to in (
            ("E20220101000000", AGILE_PRODUCT_CODE, datetime(2022, 1, 1), switch_to_go),
            ("E20260702110000", GO_PRODUCT_CODE, switch_to_go, None),
        ):
            s.add(
                model.agreement(
                    id=agreement_id,
                    energy="E",
                    product_code=product_code,
                    tariff_code=f"E-1R-{product_code}-{REGION}",
                    valid_from=valid_from,
                    valid_to=valid_to,
                )
            )
        for product_code, unit_rate in (
            (AGILE_PRODUCT_CODE, "50.00"),
            (GO_PRODUCT_CODE, "20.00"),
        ):
            s.add(
                model.product_rate(
                    id=f"{product_code}_{REGION}_202601010000",
                    product_code=product_code,
                    region=REGION,
                    valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                    valid_to=None,
                    unit_rate=Decimal(unit_rate),
                    standing_charge=STANDING_CHARGE,
                )
            )
        # 4.8 kWh on each of 1 and 2 July; the switch to Go falls inside 2 July.
        _seed_complete_day(s, date(2026, 7, 1), "0.1")
        _seed_complete_day(s, date(2026, 7, 2), "0.1")

    retriever = CostForecastRetriever(
        _source(
            mariadb_client,
            [
                _make_electricity_meter(
                    tariff_code=GO_TARIFF_CODE,
                    valid_from=switch_to_go,
                    prior_agreements=[agile_agreement],
                )
            ],
        )
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 3)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # 1 July: 4.8 kWh @ 50p + 50p standing = 290p. 2 July switched at 11:00
    # UTC: 24 half-hours @ 50p and 24 @ 20p (0.1 kWh each) + 50p = 218p.
    assert row.actual_cost_to_date == Decimal("5.08")
    # No whole day is under Go yet, so the 20p average of the published rate
    # is used (the half-Agile day would blend it to ~35p): 30 * (96p + 50p).
    assert row.projected_total_cost == pytest.approx(
        Decimal("5.08") + Decimal("43.80"), abs=Decimal("0.01")
    )
