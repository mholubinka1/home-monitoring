from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import responses
from sqlalchemy.orm import Session
from tests.weather_fixtures import (
    seed_daily_consumption_summary as _seed_daily_consumption_summary,
)
from tests.weather_fixtures import seed_weather_forecast as _seed_weather_forecast
from tests.weather_fixtures import seed_weather_observation as _seed_weather_observation

from octopus_app.common.config import (
    ApplicationSettings,
    MariaDBSettings,
    OctopusAPISettings,
    RefreshSettings,
)
from octopus_app.data.base import MonitoringClient
from octopus_app.data.cost_forecast import CostForecastRetriever
from octopus_app.data.local_day import start_of_local_day
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient

ACCOUNT_ENDPOINT = "https://api.octopus.energy/v1/accounts/A-1234ABCD"
GRID_SUPPLY_POINTS_ENDPOINT = (
    "https://api.octopus.energy/v1/industry/grid-supply-points"
)
GRAPHQL_ENDPOINT = "https://api.octopus.energy/v1/graphql/"
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


def _mock_billing_period(start: str, end: str) -> None:
    responses.add(
        responses.POST,
        GRAPHQL_ENDPOINT,
        json={"data": {"obtainKrakenToken": {"token": "kraken-jwt-token"}}},
        status=200,
    )
    responses.add(
        responses.POST,
        GRAPHQL_ENDPOINT,
        json={
            "data": {
                "account": {
                    "billingOptions": {
                        "currentBillingPeriodStartDate": start,
                        "currentBillingPeriodEndDate": end,
                        "isFixed": True,
                    }
                }
            }
        },
        status=200,
    )


# Same training/forecast fixture shape as test_cost_forecast_gas.py's
# tracer-bullet test -- a perfectly linear kWh = 20 +
# 2*heating-degree-days(max_temp) relationship over the trailing 7 days.
# Reusing the same numbers means the test below can assert the identical
# known-correct total (proven exact in the tracer-bullet test) rather than
# re-deriving new arithmetic -- if it doesn't match, the real wiring
# diverges from the already-verified regression logic.
_TRAINING_DAYS_AND_TEMPS = [
    (date(2026, 6, 30), 15.0, "21.0"),
    (date(2026, 7, 1), 12.0, "27.0"),
    (date(2026, 7, 2), 9.0, "33.0"),
    (date(2026, 7, 3), 6.0, "39.0"),
    (date(2026, 7, 4), 3.0, "45.0"),
    (date(2026, 7, 5), 0.0, "51.0"),
    (date(2026, 7, 6), -3.0, "57.0"),
]


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

    for day, max_temp, kwh in _TRAINING_DAYS_AND_TEMPS:
        _seed_daily_consumption_summary(s, day, kwh)
        _seed_weather_observation(s, day, max_temp)

    remaining_day = date(2026, 7, 7)
    billing_period_end = date(2026, 8, 6)
    while remaining_day <= billing_period_end:
        _seed_weather_forecast(s, remaining_day, -6.0)
        remaining_day += timedelta(days=1)


@responses.activate
def test_gas_weather_regression_runs_through_the_real_monitoring_client_wiring(
    mariadb_client: MariaDBClient,
) -> None:
    # This is the one test in the suite that constructs the REAL
    # MonitoringClient (the production CostForecastSource, wired in
    # main.py) rather than test_cost_forecast_gas.py's hand-maintained
    # _RealCostForecastSource fake. That fake independently satisfies the
    # CostForecastSource Protocol by delegating to MariaDBClient directly --
    # it cannot catch a regression in MonitoringClient's OWN delegation
    # (e.g. a swapped argument, a method never added). This exact gap
    # already surfaced once during #511's development: mypy caught
    # MonitoringClient missing the three new weather-read methods, but
    # nothing in the test suite would have. This test closes that gap for
    # the gas weather-regression path specifically.
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

    settings = ApplicationSettings(
        octopus=OctopusAPISettings(account_number="A-1234ABCD", api_key="sk_live_test"),
        mariadb=MariaDBSettings(
            host="localhost",
            port=3306,
            database="octopus",
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

    # Same expected total as test_cost_forecast_gas.py's tracer-bullet test
    # (identical fixture shape): £3.65 actual-to-date + £145.70 remaining
    # (31 days * 63.0 kWh/day @ 7.00p + 29.00p standing) = £149.35.
    assert gas_row.projected_total_cost == Decimal("149.35")
