from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import responses

from octopus_app.data.cost_forecast import CostForecastRetriever
from octopus_app.data.local_day import start_of_local_day
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient

from .test_cost_forecast_gap_pricing import (
    AGILE_PRODUCT_CODE,
    REGION,
    _flat_agile_forecast,
    _make_electricity_meter,
    _mock_billing_period,
    _seed_agile_forecast,
    _seed_complete_day,
    _source,
)
from .test_cost_forecast_gas import (
    GAS_PRODUCT_CODE,
    PRODUCT_CODE,
)
from .test_cost_forecast_gas import _make_electricity_meter as make_electricity_meter
from .test_cost_forecast_gas import _make_gas_meter as make_gas_meter
from .test_cost_forecast_gas import _source as gas_source


def _agile_rate(
    s_id: str, valid_from: datetime, valid_to: datetime | None, unit_rate: str
) -> model.product_rate:
    return model.product_rate(
        id=f"{AGILE_PRODUCT_CODE}_{REGION}_{s_id}",
        product_code=AGILE_PRODUCT_CODE,
        region=REGION,
        valid_from=valid_from,
        valid_to=valid_to,
        unit_rate=Decimal(unit_rate),
        standing_charge=Decimal("50.00"),
    )


@responses.activate
def test_a_past_gap_day_with_an_upstream_rate_hole_is_estimated_and_flagged(
    mariadb_client: MariaDBClient,
) -> None:
    # Oct3 (BST: 23:00 UTC Oct2 to 23:00 UTC Oct3) is a past trailing gap
    # with a two-hour hole in the published rates (21:00-23:00 UTC). The
    # forecast is still written, with the hole priced at that day's
    # time-weighted average published unit rate, and flagged as estimated.
    _mock_billing_period("2026-10-03", "2026-11-03")
    _seed_agile_forecast(
        mariadb_client, _flat_agile_forecast(date(2026, 10, 4), 31, "15.00")
    )

    with mariadb_client.session_write_scope() as s:
        s.add(
            model.agreement(
                id="E20220101000000",
                energy="E",
                product_code=AGILE_PRODUCT_CODE,
                tariff_code=f"E-1R-{AGILE_PRODUCT_CODE}-{REGION}",
                valid_from=datetime(2022, 1, 1, tzinfo=UTC),
                valid_to=None,
            )
        )
        # Published: 10p until 12:00 UTC Oct3 (13h of Oct3 local), 20p until
        # 21:00 UTC (9h), then nothing until 23:00 UTC (2h hole), then 10p.
        s.add(
            _agile_rate(
                "a",
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
                "10.00",
            )
        )
        s.add(
            _agile_rate(
                "b",
                datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
                datetime(2026, 10, 3, 21, 0, tzinfo=UTC),
                "20.00",
            )
        )
        s.add(_agile_rate("c", datetime(2026, 10, 3, 23, 0, tzinfo=UTC), None, "10.00"))
        _seed_complete_day(s, date(2026, 10, 2), "0.1")
        # No consumption at all for Oct3 (a past trailing gap by Oct4).

    retriever = CostForecastRetriever(
        _source(
            mariadb_client,
            [
                _make_electricity_meter(
                    tariff_code=f"E-1R-{AGILE_PRODUCT_CODE}-{REGION}"
                )
            ],
        )
    )
    retriever.refresh(as_of=datetime(2026, 10, 4, 4, 0, tzinfo=UTC))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    assert row.rates_estimated is True
    assert row.estimated_days == "2026-10-03"
    # Oct2 (the 4.8 kWh real day, 10p throughout): (48.00+50.00)/100 = 0.98.
    # Oct3 (4.8 kWh estimated): published 22h average is
    # (13*10 + 9*20)/22 = 310/22 p, applied to the 2h hole too, so the whole
    # day prices at 4.8*310/22 p variable + 50.00p standing.
    # Today (Oct4): standing charge only, 50.00/100 = 0.50.
    oct3 = (Decimal("4.8") * Decimal(310) / Decimal(22) + Decimal("50.00")) / 100
    expected = Decimal("0.98") + oct3 + Decimal("0.50")
    # Persisted at Numeric(9,2), so compare at pence precision.
    assert row.actual_cost_to_date == expected.quantize(Decimal("0.01"))


def _seed_agreement(s) -> None:
    s.add(
        model.agreement(
            id="E20220101000000",
            energy="E",
            product_code=AGILE_PRODUCT_CODE,
            tariff_code=f"E-1R-{AGILE_PRODUCT_CODE}-{REGION}",
            valid_from=datetime(2022, 1, 1, tzinfo=UTC),
            valid_to=None,
        )
    )


def _seed_days_each_missing_their_last_hour(s, days: list[date]) -> None:
    for day in days:
        start = start_of_local_day(day)
        s.add(_agile_rate(f"hole{day}", start, start + timedelta(hours=23), "10.00"))


@responses.activate
def test_a_fourth_rate_estimated_day_fails_the_refresh_and_writes_nothing(
    mariadb_client: MariaDBClient,
) -> None:
    # Sep30 is real; Oct1-Oct4 are four past gap days, each with an upstream
    # rate hole. Estimating more than three days' rates is treated as a
    # systemic upstream problem, not a blip, so the refresh fails loudly.
    _mock_billing_period("2026-10-01", "2026-10-31")
    _seed_agile_forecast(
        mariadb_client, _flat_agile_forecast(date(2026, 10, 5), 31, "15.00")
    )

    with mariadb_client.session_write_scope() as s:
        _seed_agreement(s)
        s.add(
            _agile_rate(
                "sep30",
                start_of_local_day(date(2026, 9, 30)),
                start_of_local_day(date(2026, 10, 1)),
                "10.00",
            )
        )
        _seed_days_each_missing_their_last_hour(
            s, [date(2026, 10, d) for d in (1, 2, 3, 4)]
        )
        s.add(
            _agile_rate("today", start_of_local_day(date(2026, 10, 5)), None, "10.00")
        )
        _seed_complete_day(s, date(2026, 9, 30), "0.1")

    retriever = CostForecastRetriever(
        _source(
            mariadb_client,
            [
                _make_electricity_meter(
                    tariff_code=f"E-1R-{AGILE_PRODUCT_CODE}-{REGION}"
                )
            ],
        )
    )

    with pytest.raises(RuntimeError, match="at most 3"):
        retriever.refresh(as_of=datetime(2026, 10, 5, 4, 0, tzinfo=UTC))

    with mariadb_client.session_read_scope() as session:
        assert session.query(model.cost_forecast).count() == 0


def _refresh_oct4_after_seeding_oct3_rates(
    mariadb_client: MariaDBClient,
    rate_ids_and_windows: list[tuple[str, datetime, datetime | None]],
) -> None:
    # Oct3 (BST) is a past trailing gap by as_of Oct4 04:00 UTC; the rates
    # given decide how much of it is published. Today's rate always exists.
    with mariadb_client.session_write_scope() as s:
        for s_id, valid_from, valid_to in rate_ids_and_windows:
            s.add(_agile_rate(s_id, valid_from, valid_to, "10.00"))


def _retriever(mariadb_client: MariaDBClient) -> CostForecastRetriever:
    return CostForecastRetriever(
        _source(
            mariadb_client,
            [
                _make_electricity_meter(
                    tariff_code=f"E-1R-{AGILE_PRODUCT_CODE}-{REGION}"
                )
            ],
        )
    )


def _seed_oct3_gap_scenario(mariadb_client: MariaDBClient) -> None:
    _mock_billing_period("2026-10-03", "2026-11-03")
    _seed_agile_forecast(
        mariadb_client, _flat_agile_forecast(date(2026, 10, 4), 31, "15.00")
    )
    with mariadb_client.session_write_scope() as s:
        _seed_agreement(s)
        _seed_complete_day(s, date(2026, 10, 2), "0.1")


AS_OF = datetime(2026, 10, 4, 4, 0, tzinfo=UTC)


@responses.activate
def test_a_past_gap_day_with_fully_published_rates_is_not_flagged(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_oct3_gap_scenario(mariadb_client)
    _refresh_oct4_after_seeding_oct3_rates(
        mariadb_client, [("all", datetime(2026, 1, 1, tzinfo=UTC), None)]
    )

    _retriever(mariadb_client).refresh(as_of=AS_OF)

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()
    assert row.rates_estimated is False
    assert row.estimated_days is None


@responses.activate
def test_the_flag_clears_on_a_later_refresh_once_the_missing_rates_arrive(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_oct3_gap_scenario(mariadb_client)
    _refresh_oct4_after_seeding_oct3_rates(
        mariadb_client,
        [
            (
                "a",
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 10, 3, 21, 0, tzinfo=UTC),
            ),
            ("c", datetime(2026, 10, 3, 23, 0, tzinfo=UTC), None),
        ],
    )
    _retriever(mariadb_client).refresh(as_of=AS_OF)

    # The missing 21:00-23:00 UTC rate is published before the next refresh.
    _mock_billing_period("2026-10-03", "2026-11-03")
    _refresh_oct4_after_seeding_oct3_rates(
        mariadb_client,
        [
            (
                "b",
                datetime(2026, 10, 3, 21, 0, tzinfo=UTC),
                datetime(2026, 10, 3, 23, 0, tzinfo=UTC),
            ),
        ],
    )
    _retriever(mariadb_client).refresh(as_of=AS_OF)

    with mariadb_client.session_read_scope() as session:
        latest = (
            session.query(model.cost_forecast)
            .order_by(model.cost_forecast.id.desc())
            .first()
        )
    assert latest.rates_estimated is False
    assert latest.estimated_days is None


@responses.activate
def test_a_past_gap_day_with_no_published_rates_at_all_still_fails_the_refresh(
    mariadb_client: MariaDBClient,
) -> None:
    # Guards the boundary with the no-rates fallback (issue #593): with no
    # published segment there is no average to estimate from.
    _seed_oct3_gap_scenario(mariadb_client)
    _refresh_oct4_after_seeding_oct3_rates(
        mariadb_client, [("c", datetime(2026, 10, 3, 23, 0, tzinfo=UTC), None)]
    )

    with pytest.raises(RuntimeError, match="No product_rate found"):
        _retriever(mariadb_client).refresh(as_of=AS_OF)

    with mariadb_client.session_read_scope() as session:
        assert session.query(model.cost_forecast).count() == 0


@responses.activate
def test_a_gas_past_gap_day_with_an_upstream_rate_hole_is_estimated_and_flagged(
    mariadb_client: MariaDBClient,
) -> None:
    # Gas shares the electricity code path: only the gas row is flagged,
    # because only the gas rates have a hole (Oct3 21:00-23:00 UTC).
    _mock_billing_period("2026-10-03", "2026-11-03")

    with mariadb_client.session_write_scope() as s:
        s.add(
            model.agreement(
                id="E20220101000000",
                energy="E",
                product_code=PRODUCT_CODE,
                tariff_code=f"E-1R-{PRODUCT_CODE}-{REGION}",
                valid_from=datetime(2022, 1, 1, tzinfo=UTC),
                valid_to=None,
            )
        )
        s.add(
            model.product_rate(
                id=f"{PRODUCT_CODE}_{REGION}_202601010000",
                product_code=PRODUCT_CODE,
                region=REGION,
                valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                valid_to=None,
                unit_rate=Decimal("20.00"),
                standing_charge=Decimal("48.00"),
            )
        )
        _seed_complete_day(s, date(2026, 10, 2), "0.1", energy="E")
        s.add(
            model.agreement(
                id="G20220101000000",
                energy="G",
                product_code=GAS_PRODUCT_CODE,
                tariff_code=f"G-1R-{GAS_PRODUCT_CODE}-{REGION}",
                valid_from=datetime(2022, 1, 1, tzinfo=UTC),
                valid_to=None,
            )
        )
        for s_id, valid_from, valid_to in [
            (
                "a",
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 10, 3, 21, 0, tzinfo=UTC),
            ),
            ("c", datetime(2026, 10, 3, 23, 0, tzinfo=UTC), None),
        ]:
            s.add(
                model.product_rate(
                    id=f"{GAS_PRODUCT_CODE}_{REGION}_{s_id}",
                    product_code=GAS_PRODUCT_CODE,
                    region=REGION,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    unit_rate=Decimal("7.00"),
                    standing_charge=Decimal("29.00"),
                )
            )
        _seed_complete_day(s, date(2026, 10, 2), "1.0", energy="G")

    CostForecastRetriever(
        gas_source(mariadb_client, [make_electricity_meter(), make_gas_meter()])
    ).refresh(as_of=AS_OF)

    with mariadb_client.session_read_scope() as session:
        gas_row = session.query(model.cost_forecast).filter_by(energy="G").one()
        electricity_row = session.query(model.cost_forecast).filter_by(energy="E").one()

    assert gas_row.rates_estimated is True
    assert gas_row.estimated_days == "2026-10-03"
    assert electricity_row.rates_estimated is False
    assert electricity_row.estimated_days is None
    # Oct2 (48 kWh @ 7p + 29p) = 3.65; Oct3 (48 kWh priced flat 7p across
    # the hole, + 29p) = 3.65; today standing charge only = 0.29.
    assert gas_row.actual_cost_to_date == Decimal("7.59")
