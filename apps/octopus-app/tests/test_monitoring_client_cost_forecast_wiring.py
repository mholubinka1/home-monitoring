from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import responses
from sqlalchemy.orm import Session
from tests.weather_fixtures import mock_billing_period as _mock_billing_period
from tests.weather_fixtures import seed_heating_forecast as _seed_heating_forecast

from common.local_day import start_of_local_day
from octopus_app.common.config import (
    ApplicationSettings,
    MariaDBSettings,
    OctopusAPISettings,
    RefreshSettings,
)
from octopus_app.data.base import MonitoringClient
from octopus_app.data.cost_forecast import CostForecastRetriever
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient

ACCOUNT_ENDPOINT = "https://api.octopus.energy/v1/accounts/A-1234ABCD"
GRID_SUPPLY_POINTS_ENDPOINT = (
    "https://api.octopus.energy/v1/industry/grid-supply-points"
)
PRODUCT_CODE = "VAR-24-10-01"
GAS_PRODUCT_CODE = "VAR-22-11-01"
REGION = "H"

ACCOUNT_RESPONSE = {
    "properties": [
        {
            "postcode": "AB1 2CD",
            "address_line_1": "1 Test Street",
            "address_line_2": "",
            "address_line_3": "",
            "town": "Testville",
            "county": "",
            "electricity_meter_points": [
                {
                    "mpan": "1234567890123",
                    "meters": [{"serial_number": "00A1234567"}],
                    "agreements": [
                        {
                            "tariff_code": f"E-1R-{PRODUCT_CODE}-{REGION}",
                            "valid_from": "2022-01-01T00:00:00+00:00",
                            "valid_to": None,
                        }
                    ],
                }
            ],
            "gas_meter_points": [
                {
                    "mprn": "1234567890",
                    "meters": [{"serial_number": "G00A123456"}],
                    "agreements": [
                        {
                            "tariff_code": f"G-1R-{GAS_PRODUCT_CODE}-{REGION}",
                            "valid_from": "2022-01-01T00:00:00+00:00",
                            "valid_to": None,
                        }
                    ],
                }
            ],
        }
    ]
}

GRID_SUPPLY_POINTS_RESPONSE = {"results": [{"group_id": f"_{REGION}"}]}


# Same billing-period and gas/electricity fixtures as test_cost_forecast_gas.py,
# so the total below is the one that file's tests already prove by hand -- if
# it does not match, the real wiring diverges from the verified logic.


def _seed_gas_and_electricity_fixtures(s: Session) -> None:
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
    s.add(
        model.product_rate(
            id=f"{GAS_PRODUCT_CODE}_{REGION}_202601010000",
            product_code=GAS_PRODUCT_CODE,
            region=REGION,
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
            valid_to=None,
            unit_rate=Decimal("7.00"),
            standing_charge=Decimal("29.00"),
        )
    )
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
    # One elapsed gas day (2026-07-06, the billing period's start after
    # Kraken's date-shift) so actual_cost_to_date is non-zero, mirroring
    # test_cost_forecast_gas.py's fixtures.
    start = start_of_local_day(date(2026, 7, 6))
    for slot in range(48):
        slot_start = start + timedelta(minutes=30 * slot)
        s.add(
            model.consumption(
                id=f"G{slot_start.strftime('%Y%m%d%H%M%S')}",
                energy="G",
                period_from=slot_start,
                period_to=slot_start + timedelta(minutes=30),
                raw_value=Decimal("1.0"),
                unit="kWh",
                est_kwh=Decimal("1.0"),
            )
        )


@responses.activate
def test_gas_heating_model_forecast_runs_through_the_real_monitoring_client_wiring(
    mariadb_client: MariaDBClient,
) -> None:
    # This is the one test in the suite that constructs the REAL
    # MonitoringClient (the production CostForecastSource, wired in
    # main.py) rather than test_cost_forecast_gas.py's hand-maintained
    # _RealCostForecastSource fake. That fake independently satisfies the
    # CostForecastSource Protocol by delegating to MariaDBClient directly --
    # it cannot catch a regression in MonitoringClient's OWN delegation
    # (e.g. a swapped argument, a method never added). This test closes that
    # gap for the gas heating-model path: read_latest_usable_heating_model
    # and read_heating_forecast_days.
    responses.add(responses.GET, ACCOUNT_ENDPOINT, json=ACCOUNT_RESPONSE, status=200)
    responses.add(
        responses.GET,
        GRID_SUPPLY_POINTS_ENDPOINT,
        json=GRID_SUPPLY_POINTS_RESPONSE,
        status=200,
    )
    _mock_billing_period("2026-07-07", "2026-08-07")

    with mariadb_client.session_write_scope() as s:
        _seed_gas_and_electricity_fixtures(s)
    _seed_heating_forecast(
        mariadb_client,
        {date(2026, 7, 7) + timedelta(days=offset): 63.0 for offset in range(7)},
    )

    settings = ApplicationSettings(
        octopus=OctopusAPISettings(account_number="A-1234ABCD", api_key="sk_live_test"),
        mariadb=MariaDBSettings(
            host="localhost",
            port=3306,
            # "main" (not "octopus"): must agree with the mariadb_client
            # fixture's own map -- see ADR-0025.
            database="main",
            username="test",
            password="test",
        ),
        data_refresh=RefreshSettings(refresh_interval_hours=4, retention_days=45),
    )
    client = MonitoringClient(settings)

    retriever = CostForecastRetriever(client)
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        gas_row = session.query(model.cost_forecast).filter_by(energy="G").one()

    # The model covers a week, as the job writes it: £3.65 actual-to-date +
    # £120.50 remaining (7 days * 63.0 kWh and 24 days * the flat 48.0 kWh,
    # all @ 7.00p, plus 31 days of 29.00p standing) = £124.15.
    assert gas_row.projected_total_cost == Decimal("124.15")
