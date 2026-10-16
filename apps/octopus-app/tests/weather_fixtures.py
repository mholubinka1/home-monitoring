from datetime import UTC, date, datetime

import responses

from octopus_app.data.model import HeatingForecastRecord, HeatingModelRecord
from octopus_app.data.mysql.client import MariaDBClient

GRAPHQL_ENDPOINT = "https://api.octopus.energy/v1/graphql/"


def mock_billing_period(start: str, end: str, is_fixed: bool = True) -> None:
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
                        "isFixed": is_fixed,
                    }
                }
            }
        },
        status=200,
    )


def heating_model_record(
    fitted_at: datetime, usable: bool = True
) -> HeatingModelRecord:
    return HeatingModelRecord(
        fitted_at=fitted_at,
        window_start=date(2025, 7, 1),
        window_end=date(2026, 6, 30),
        usable=usable,
        unusable_reason=None if usable else "the fit explains too little",
        days_used=300,
        threshold=13.0,
        upper_threshold=14.0,
        slope=3.76,
        weight=0.8,
        r_squared=0.69,
        baseload=8.0,
        baseload_measured_days=200,
        baseload_interpolated_days=50,
        baseload_held_days=5,
        noise_base=3.4,
        noise_per_degree_day=1.2,
        noise_floor=1.0,
        thermostat_threshold=None,
        heating_days=None,
        non_heating_days=None,
        label="estimated",
    )


def seed_heating_forecast(
    client: MariaDBClient,
    expected_gas: dict[date, float],
    fitted_at: datetime = datetime(2026, 7, 6, 4, 0, tzinfo=UTC),
    usable: bool = True,
) -> int:
    """Stores one heating model and a forecast row per day, tied to it."""
    model_id = client.write_heating_model(heating_model_record(fitted_at, usable))
    client.replace_heating_forecast_days(
        model_id,
        [
            HeatingForecastRecord(day, 5.0, 5.0, gas)
            for day, gas in expected_gas.items()
        ],
        fitted_at,
    )
    return model_id
