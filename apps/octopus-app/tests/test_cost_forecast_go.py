from datetime import UTC, date, datetime, timedelta
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
    _seed_july_6_go_rates(s)


def _seed_july_6_go_rates(s) -> None:
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
def test_go_projection_prices_each_slot_with_the_usual_usage_at_that_time_of_day(
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


@pytest.mark.parametrize(
    ("as_of", "remaining_unit_pence"),
    [
        (datetime(2026, 7, 6, 12, 0, tzinfo=UTC), Decimal(950)),
        # 13:15 local: the 13:00 half-hour (15p) is half elapsed.
        (datetime(2026, 7, 6, 12, 15, tzinfo=UTC), Decimal("942.5")),
    ],
)
@responses.activate
def test_go_projection_spreads_usage_evenly_across_the_day_without_a_whole_day_yet(
    mariadb_client: MariaDBClient, as_of: datetime, remaining_unit_pence: Decimal
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
    # excluded as partial, so no real elapsed day exists to learn usage from.
    retriever.refresh(as_of=as_of)

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # actual: 24 kWh @ 30p + 50p standing = £7.70
    assert row.actual_cost_to_date == Decimal("7.70")
    # Even usage: 0.5 kWh per half-hour. Rest of 6 July from 13:00 local is 22
    # peak slots (330p); 7 July repeats the latest fully published day's
    # pattern (3 peak, 10 cheap, 35 peak slots: 570p + 50p = 620p). Total
    # 950p of unit charges (less any elapsed part of the current half-hour)
    # plus one remaining day's 50p standing charge.
    expected_remaining = (remaining_unit_pence + STANDING_CHARGE) / 100
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


@responses.activate
def test_go_projection_prices_a_later_agreement_from_its_own_start(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-08-07")
    fixed_product_code = "VAR-24-10-01"
    changeover = start_of_local_day(date(2026, 7, 20))
    go_agreement = Agreement(
        tariff_code=GO_TARIFF_CODE,
        valid_from=datetime(2022, 1, 1, tzinfo=UTC),
        valid_to=changeover,
    )
    with mariadb_client.session_write_scope() as s:
        for product_code, unit_rate in (
            (GO_PRODUCT_CODE, "20.00"),
            (fixed_product_code, "30.00"),
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
        s.add(
            model.agreement(
                id="E20220101000000",
                energy="E",
                product_code=GO_PRODUCT_CODE,
                tariff_code=GO_TARIFF_CODE,
                valid_from=datetime(2022, 1, 1),
                valid_to=changeover,
            )
        )
        _seed_complete_day(s, date(2026, 7, 6), "0.1")

    retriever = CostForecastRetriever(
        _source(
            mariadb_client,
            [
                _make_electricity_meter(
                    tariff_code=f"E-1R-{fixed_product_code}-{REGION}",
                    valid_from=changeover,
                    prior_agreements=[go_agreement],
                )
            ],
        )
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # The Go agreement is the current one on 7 July: 13 days (7-19 July) at
    # 20p, then 18 days (20 July-6 August) priced by the fixed product at 30p
    # from the changeover: 4.8 kWh * (13*20 + 18*30) = 3840p, plus 31 days of
    # standing charge at 50p (the standing charge stays the current
    # agreement's), plus the elapsed day (4.8 kWh @ 20p + 50p = 146p).
    assert row.projected_total_cost == pytest.approx(
        Decimal("1.46") + (Decimal(3840) + Decimal(1550)) / 100,
        abs=Decimal("0.01"),
    )


@responses.activate
def test_go_usage_pattern_ignores_days_on_the_previous_tariff(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-06", "2026-08-07")
    switch_to_go = datetime(2026, 7, 5, 23, 0, tzinfo=UTC)
    agile_agreement = Agreement(
        tariff_code=f"E-1R-{AGILE_PRODUCT_CODE}-{REGION}",
        valid_from=datetime(2022, 1, 1, tzinfo=UTC),
        valid_to=switch_to_go,
    )
    with mariadb_client.session_write_scope() as s:
        for agreement_id, product_code, valid_from, valid_to in (
            ("E20220101000000", AGILE_PRODUCT_CODE, datetime(2022, 1, 1), switch_to_go),
            ("E20260705230000", GO_PRODUCT_CODE, switch_to_go, None),
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
        s.add(
            model.product_rate(
                id=f"{AGILE_PRODUCT_CODE}_{REGION}_202601010000",
                product_code=AGILE_PRODUCT_CODE,
                region=REGION,
                valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                valid_to=None,
                unit_rate=Decimal("50.00"),
                standing_charge=STANDING_CHARGE,
            )
        )
        _seed_july_6_go_rates(s)
        # 5 July on Agile: 7.8 kWh spread evenly (0.1625 per half-hour).
        _seed_complete_day(s, date(2026, 7, 5), "0.1625")
        # 6 July on Go: 0.4 kWh in the 10 cheap slots, 0.1 in the other 38.
        _seed_complete_day(s, date(2026, 7, 6), "0.1")
        for row in s.query(model.consumption).all():
            if (
                datetime(2026, 7, 6, 0, 30)
                <= row.period_from
                < datetime(2026, 7, 6, 5, 30)
            ):
                row.est_kwh = row.raw_value = Decimal("0.4")

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
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # actual: 5 July (7.8 kWh @ 50p + 50p) + 6 July (154p + 50p) = £6.44
    assert row.actual_cost_to_date == Decimal("6.44")
    # Both days total 7.8 kWh, but only 6 July's cheap-window-heavy shape is
    # used, so each of the 31 remaining days costs 154p + 50p, as on 6 July.
    assert row.projected_total_cost == pytest.approx(
        Decimal("6.44") + Decimal(31) * Decimal("2.04"), abs=Decimal("0.01")
    )


@responses.activate
def test_go_pattern_beyond_published_rates_follows_the_local_clock_across_a_clock_change(
    mariadb_client: MariaDBClient,
) -> None:
    # Only 25 October (the 25-hour clock-change day) is published. Its cheap
    # window is 00:30-05:30 local, i.e. 6 elapsed hours (23:30Z-05:30Z), and
    # 26-27 October must repeat it by local clock time: 10 cheap half-hours,
    # not the 12 an elapsed-hours mapping would give.
    _mock_billing_period("2026-10-25", "2026-10-28")
    clock_change_day_start = datetime(2026, 10, 24, 23, 0, tzinfo=UTC)
    windows = [
        (clock_change_day_start, datetime(2026, 10, 24, 23, 30), PEAK_RATE),
        (datetime(2026, 10, 24, 23, 30), datetime(2026, 10, 25, 5, 30), OFF_PEAK_RATE),
        (datetime(2026, 10, 25, 5, 30), datetime(2026, 10, 26, 0, 0), PEAK_RATE),
    ]
    with mariadb_client.session_write_scope() as s:
        s.add(
            model.agreement(
                id="E20220101000000",
                energy="E",
                product_code=GO_PRODUCT_CODE,
                tariff_code=GO_TARIFF_CODE,
                valid_from=datetime(2022, 1, 1),
                valid_to=None,
            )
        )
        s.add(
            model.product_rate(
                id=f"{GO_PRODUCT_CODE}_{REGION}_202201010000",
                product_code=GO_PRODUCT_CODE,
                region=REGION,
                valid_from=datetime(2022, 1, 1, tzinfo=UTC),
                valid_to=clock_change_day_start,
                unit_rate=Decimal("20.00"),
                standing_charge=STANDING_CHARGE,
            )
        )
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
        _seed_complete_day(s, date(2026, 10, 24), "0.1")

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter(tariff_code=GO_TARIFF_CODE)])
    )
    retriever.refresh(as_of=clock_change_day_start)

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # actual: 24 October, 4.8 kWh @ 20p + 50p standing = £1.46.
    assert row.actual_cost_to_date == Decimal("1.46")
    # 25 October (published, 50 slots of 0.096 kWh, 12 of them cheap):
    # 120.96p. 26 and 27 October (48 slots of 0.1 kWh, 10 cheap): 124p each.
    # Three remaining days of 50p standing charge.
    remaining_pence = Decimal("120.96") + 2 * Decimal(124) + 3 * STANDING_CHARGE
    assert row.projected_total_cost == pytest.approx(
        Decimal("1.46") + remaining_pence / 100, abs=Decimal("0.01")
    )


FIXED_PRODUCT_CODE = "VAR-24-10-01"


def _seed_flat_rate(s, product_code: str, unit_rate: str) -> None:
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


def _seed_go_agreement_row(s, valid_to: datetime | None) -> None:
    s.add(
        model.agreement(
            id="E20220101000000",
            energy="E",
            product_code=GO_PRODUCT_CODE,
            tariff_code=GO_TARIFF_CODE,
            valid_from=datetime(2022, 1, 1),
            valid_to=valid_to,
        )
    )


@responses.activate
def test_go_usage_pattern_carries_across_a_renewal_onto_the_same_product(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-08-07")
    renewal = start_of_local_day(date(2026, 7, 7))
    first_term = Agreement(
        tariff_code=GO_TARIFF_CODE,
        valid_from=datetime(2022, 1, 1, tzinfo=UTC),
        valid_to=renewal,
    )
    with mariadb_client.session_write_scope() as s:
        _seed_go_agreement_row(s, renewal)
        _seed_july_6_go_rates(s)
        _seed_complete_day(s, date(2026, 7, 6), "0.1")
        for row in s.query(model.consumption).all():
            if (
                datetime(2026, 7, 6, 0, 30)
                <= row.period_from
                < datetime(2026, 7, 6, 5, 30)
            ):
                row.est_kwh = row.raw_value = Decimal("0.4")

    retriever = CostForecastRetriever(
        _source(
            mariadb_client,
            [
                _make_electricity_meter(
                    tariff_code=GO_TARIFF_CODE,
                    valid_from=renewal,
                    prior_agreements=[first_term],
                )
            ],
        )
    )
    retriever.refresh(as_of=renewal)

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # The new term has no whole day yet, but 6 July's cheap-window-heavy
    # pattern under the first term (same product) still shapes the usage:
    # 154p + 50p standing for each of the 31 remaining days. An even spread
    # would price the cheap-window usage at peak rates instead.
    assert row.projected_total_cost == pytest.approx(
        Decimal("2.04") + Decimal(31) * Decimal("2.04"), abs=Decimal("0.01")
    )


@responses.activate
def test_a_gap_between_agreements_is_priced_by_the_one_that_had_begun(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-08-07")
    go_ends = start_of_local_day(date(2026, 7, 20))
    fixed_begins = start_of_local_day(date(2026, 7, 22))
    go_agreement = Agreement(
        tariff_code=GO_TARIFF_CODE,
        valid_from=datetime(2022, 1, 1, tzinfo=UTC),
        valid_to=go_ends,
    )
    # A cancelled switch to Agile that never ran sits at the start of the gap.
    never_ran = Agreement(
        tariff_code=f"E-1R-{AGILE_PRODUCT_CODE}-{REGION}",
        valid_from=go_ends,
        valid_to=go_ends,
    )
    with mariadb_client.session_write_scope() as s:
        _seed_go_agreement_row(s, go_ends)
        _seed_flat_rate(s, GO_PRODUCT_CODE, "20.00")
        _seed_flat_rate(s, FIXED_PRODUCT_CODE, "30.00")
        _seed_complete_day(s, date(2026, 7, 6), "0.1")

    retriever = CostForecastRetriever(
        _source(
            mariadb_client,
            [
                _make_electricity_meter(
                    tariff_code=f"E-1R-{FIXED_PRODUCT_CODE}-{REGION}",
                    valid_from=fixed_begins,
                    prior_agreements=[go_agreement, never_ran],
                )
            ],
        )
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # 20-21 July fall between the agreements and stay on Go (20p): 15 days
    # (7-21 July) at 20p, then 16 days at the fixed 30p:
    # 4.8 kWh * (15*20 + 16*30) = 3744p, plus 31 days of standing charge
    # (1550p) and the elapsed day (146p).
    assert row.projected_total_cost == pytest.approx(
        Decimal("1.46") + (Decimal(3744) + Decimal(1550)) / 100,
        abs=Decimal("0.01"),
    )


@responses.activate
def test_a_later_agreement_with_no_published_rates_fails_the_refresh_without_a_row(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-08-07")
    changeover = start_of_local_day(date(2026, 7, 20))
    go_agreement = Agreement(
        tariff_code=GO_TARIFF_CODE,
        valid_from=datetime(2022, 1, 1, tzinfo=UTC),
        valid_to=changeover,
    )
    with mariadb_client.session_write_scope() as s:
        _seed_go_agreement_row(s, changeover)
        _seed_flat_rate(s, GO_PRODUCT_CODE, "20.00")
        _seed_complete_day(s, date(2026, 7, 6), "0.1")

    retriever = CostForecastRetriever(
        _source(
            mariadb_client,
            [
                _make_electricity_meter(
                    tariff_code=f"E-1R-{FIXED_PRODUCT_CODE}-{REGION}",
                    valid_from=changeover,
                    prior_agreements=[go_agreement],
                )
            ],
        )
    )

    with pytest.raises(RuntimeError, match="No fully published day found"):
        retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        assert session.query(model.cost_forecast).count() == 0


@responses.activate
def test_go_usage_pattern_carries_across_a_gap_between_agreements_on_the_same_product(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-08-07")
    first_term_ends = datetime(2026, 7, 6, 23, 0, tzinfo=UTC)
    second_term_begins = datetime(2026, 7, 7, 0, 0, tzinfo=UTC)
    first_term = Agreement(
        tariff_code=GO_TARIFF_CODE,
        valid_from=datetime(2022, 1, 1, tzinfo=UTC),
        valid_to=first_term_ends,
    )
    with mariadb_client.session_write_scope() as s:
        _seed_go_agreement_row(s, first_term_ends)
        _seed_july_6_go_rates(s)
        # 7 July's rate covering local midday (for the day's standing charge).
        s.add(
            model.product_rate(
                id=f"{GO_PRODUCT_CODE}_{REGION}_202607070030",
                product_code=GO_PRODUCT_CODE,
                region=REGION,
                valid_from=datetime(2026, 7, 7, 0, 30, tzinfo=UTC),
                valid_to=datetime(2026, 7, 7, 12, 30, tzinfo=UTC),
                unit_rate=PEAK_RATE,
                standing_charge=STANDING_CHARGE,
            )
        )
        _seed_complete_day(s, date(2026, 7, 6), "0.1")
        for row in s.query(model.consumption).all():
            if (
                datetime(2026, 7, 6, 0, 30)
                <= row.period_from
                < datetime(2026, 7, 6, 5, 30)
            ):
                row.est_kwh = row.raw_value = Decimal("0.4")

    retriever = CostForecastRetriever(
        _source(
            mariadb_client,
            [
                _make_electricity_meter(
                    tariff_code=GO_TARIFF_CODE,
                    valid_from=second_term_begins,
                    prior_agreements=[first_term],
                )
            ],
        )
    )
    # 13:00 local on 7 July, an hour after the second term began: no whole
    # day under it yet, but 6 July under the first term (same product) shapes
    # the usage -- 7.8 kWh a day, 154p of unit charges on the repeated 6 July
    # pattern, so only the 66p of peak slots from 13:00 are left today.
    retriever.refresh(as_of=datetime(2026, 7, 7, 12, 0, tzinfo=UTC))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    remaining_days = 30
    expected_remaining = (
        Decimal(66) + remaining_days * Decimal(154) + remaining_days * STANDING_CHARGE
    ) / 100
    assert row.projected_total_cost == pytest.approx(
        row.actual_cost_to_date + expected_remaining, abs=Decimal("0.02")
    )


@responses.activate
def test_a_later_product_published_only_for_a_few_days_repeats_its_own_latest_day(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-08-07")
    changeover = start_of_local_day(date(2026, 7, 20))
    published_until = start_of_local_day(date(2026, 7, 22))
    go_agreement = Agreement(
        tariff_code=GO_TARIFF_CODE,
        valid_from=datetime(2022, 1, 1, tzinfo=UTC),
        valid_to=changeover,
    )
    with mariadb_client.session_write_scope() as s:
        _seed_go_agreement_row(s, changeover)
        _seed_flat_rate(s, GO_PRODUCT_CODE, "20.00")
        # The later product has rates for 20 and 21 July only, nothing for the
        # refresh day or its neighbours: its pattern day lies days after the
        # refresh date, so 22 July onwards must repeat 21 July.
        s.add(
            model.product_rate(
                id=f"{FIXED_PRODUCT_CODE}_{REGION}_202607192300",
                product_code=FIXED_PRODUCT_CODE,
                region=REGION,
                valid_from=changeover,
                valid_to=published_until,
                unit_rate=Decimal("30.00"),
                standing_charge=STANDING_CHARGE,
            )
        )
        _seed_complete_day(s, date(2026, 7, 6), "0.1")

    retriever = CostForecastRetriever(
        _source(
            mariadb_client,
            [
                _make_electricity_meter(
                    tariff_code=f"E-1R-{FIXED_PRODUCT_CODE}-{REGION}",
                    valid_from=changeover,
                    prior_agreements=[go_agreement],
                )
            ],
        )
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # Same totals as a fixed 30p product from 20 July: 13 days at 20p and 18 at
    # 30p of 4.8 kWh (3840p), 31 days of standing charge, and the elapsed day.
    assert row.projected_total_cost == pytest.approx(
        Decimal("1.46") + (Decimal(3840) + Decimal(1550)) / 100,
        abs=Decimal("0.01"),
    )


@responses.activate
def test_a_clock_change_day_in_the_usage_history_does_not_skew_the_shares(
    mariadb_client: MariaDBClient,
) -> None:
    # 25 October 2026 has 50 half-hours (the 01:00 and 01:30 local times occur
    # twice). With 0.1 kWh in every one, usage is even, so an ordinary day must
    # be projected evenly, not with double weight on the repeated hours.
    _mock_billing_period("2026-10-26", "2026-10-29")
    clock_change_day_start = datetime(2026, 10, 24, 23, 0, tzinfo=UTC)
    windows = [
        (clock_change_day_start, datetime(2026, 10, 24, 23, 30), PEAK_RATE),
        (datetime(2026, 10, 24, 23, 30), datetime(2026, 10, 25, 5, 30), OFF_PEAK_RATE),
        (datetime(2026, 10, 25, 5, 30), datetime(2026, 10, 26, 0, 0), PEAK_RATE),
        # The half-hour covering the refresh instant (a peak slot, as the
        # repeated pattern would also give).
        (datetime(2026, 10, 26, 0, 0), datetime(2026, 10, 26, 0, 30), PEAK_RATE),
    ]
    with mariadb_client.session_write_scope() as s:
        _seed_go_agreement_row(s, None)
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
        for slot in range(50):
            slot_start = clock_change_day_start + timedelta(minutes=30 * slot)
            s.add(
                model.consumption(
                    id=f"E{slot_start:%Y%m%d%H%M%S}",
                    energy="E",
                    period_from=slot_start,
                    period_to=slot_start + timedelta(minutes=30),
                    raw_value=Decimal("0.1"),
                    unit="kWh",
                    est_kwh=Decimal("0.1"),
                )
            )

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter(tariff_code=GO_TARIFF_CODE)])
    )
    retriever.refresh(as_of=datetime(2026, 10, 26, 0, 0, tzinfo=UTC))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # actual: 25 October, 12 cheap + 38 peak half-hours of 0.1 kWh = 126p, plus
    # 50p standing = £1.76.
    assert row.actual_cost_to_date == Decimal("1.76")
    # 26-28 October: 5.0 kWh a day spread evenly over 48 half-hours
    # (5/48 each), 10 cheap and 38 peak: 5/48 * (10*10 + 38*30) = 129.17p a
    # day, plus three days of 50p standing charge. Double-weighting the
    # repeated hour would give 126p a day and fail this.
    remaining_pence = 3 * Decimal(5) / 48 * (10 * 10 + 38 * 30) + 3 * STANDING_CHARGE
    assert row.projected_total_cost == pytest.approx(
        Decimal("1.76") + remaining_pence / 100, abs=Decimal("0.01")
    )
