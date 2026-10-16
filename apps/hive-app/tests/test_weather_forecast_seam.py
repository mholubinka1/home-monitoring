from datetime import UTC, date, datetime

import pytest
import responses

from common.local_day import set_local_timezone
from hive_app.common.config import LocationSettings
from hive_app.data.model import WeatherForecastDay
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.open_meteo_client import OpenMeteoClient

OPEN_METEO_ENDPOINT = "https://api.open-meteo.com/v1/forecast"


@responses.activate
def test_an_open_meteo_forecast_is_persisted_and_queryable(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json={
            "daily": {
                "time": ["2026-09-26", "2026-09-27"],
                "temperature_2m_max": [14.5, 12.1],
            }
        },
        status=200,
    )

    client = OpenMeteoClient(LocationSettings(latitude=51.5, longitude=-0.1))

    forecast = client.get_forecast()

    # ADR-0010: this repo buckets "day" as local time (here the default,
    # Europe/London) for consumption/cost data -- the forecast request must
    # ask Open-Meteo to bucket its daily aggregation the same way, not UTC, or
    # target_date would drift from daily_consumption_summary's day boundaries
    # around a clock change (the heating model, ADR-0030, joins the two).
    assert len(responses.calls) == 1
    assert "timezone=Europe%2FLondon" in responses.calls[0].request.url

    mariadb_client.write_weather_forecast(forecast)

    with mariadb_client.session_read_scope() as session:
        stored = (
            session.query(model.weather_forecast)
            .order_by(model.weather_forecast.target_date)
            .all()
        )

    assert len(stored) == 2
    assert stored[0].source == "open-meteo"
    assert stored[0].target_date.isoformat() == "2026-09-26"
    assert stored[0].max_temp == 14.5
    assert stored[1].target_date.isoformat() == "2026-09-27"
    assert stored[1].max_temp == 12.1


def test_writing_a_forecast_for_a_day_already_stored_upserts_rather_than_duplicates(
    mariadb_client: MariaDBClient,
) -> None:
    target_date = date(2026, 9, 26)
    first = WeatherForecastDay(
        source="open-meteo",
        target_date=target_date,
        max_temp=14.5,
        fetched_at=datetime(2026, 9, 25, 6, 0, tzinfo=UTC),
    )
    second = WeatherForecastDay(
        source="open-meteo",
        target_date=target_date,
        max_temp=16.2,
        fetched_at=datetime(2026, 9, 25, 12, 0, tzinfo=UTC),
    )

    mariadb_client.write_weather_forecast([first])
    mariadb_client.write_weather_forecast([second])

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.weather_forecast).all()

    assert len(stored) == 1
    assert stored[0].max_temp == 16.2


@responses.activate
def test_a_mismatched_length_forecast_response_is_rejected_rather_than_mispaired() -> (
    None
):
    # A malformed/truncated upstream response with unequal array lengths --
    # zip()'s default truncating behaviour would silently mispair a day's
    # temperature with the wrong date instead of surfacing the corruption.
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json={
            "daily": {
                "time": ["2026-09-26", "2026-09-27"],
                "temperature_2m_max": [14.5],
            }
        },
        status=200,
    )

    client = OpenMeteoClient(LocationSettings(latitude=51.5, longitude=-0.1))

    with pytest.raises(ValueError, match="mismatched array lengths"):
        client.get_forecast()


@responses.activate
def test_the_forecast_request_asks_for_days_in_the_configured_timezone() -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json={
            "daily": {
                "time": ["2026-09-26"],
                "temperature_2m_max": [14.5],
            }
        },
        status=200,
    )
    set_local_timezone("America/New_York")

    OpenMeteoClient(LocationSettings(latitude=40.7, longitude=-74.0)).get_forecast()

    assert "timezone=America%2FNew_York" in responses.calls[0].request.url
