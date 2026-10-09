import pytest
import requests
import responses
from schedule import Scheduler

from hive_app.common.config import LocationSettings
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.main import (
    _build_weather_retriever,
    register_weather_forecast_refresh_job,
)

OPEN_METEO_ENDPOINT = "https://api.open-meteo.com/v1/forecast"


@responses.activate
def test_the_retriever_fetches_and_persists_the_observation_from_open_meteo(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json={
            "current": {
                "time": "2026-09-25T12:00",
                "temperature_2m": 14.5,
                "relative_humidity_2m": 72,
                "surface_pressure": 1012.3,
                "wind_speed_10m": 8.1,
                "precipitation": 0.0,
            }
        },
        status=200,
    )

    retriever = _build_weather_retriever(
        LocationSettings(latitude=51.5, longitude=-0.1), mariadb_client
    )
    retriever.refresh()

    assert len(responses.calls) == 1
    assert responses.calls[0].request.url.startswith(OPEN_METEO_ENDPOINT)

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.weather_observation).all()

    assert len(stored) == 1
    assert stored[0].source == "open-meteo"


@responses.activate
def test_an_open_meteo_observation_failure_raises_after_exactly_one_request_and_logs_no_fallback(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    # No other endpoint is registered -- any second request (a retry as a
    # fallback, an ntfy alert) would raise ConnectionError instead of the
    # plain HTTPError this test expects.
    responses.add(responses.GET, OPEN_METEO_ENDPOINT, status=500)

    retriever = _build_weather_retriever(
        LocationSettings(latitude=51.5, longitude=-0.1), mariadb_client
    )

    with pytest.raises(requests.HTTPError):
        retriever.refresh()

    assert len(responses.calls) == 1
    assert "fall" not in caplog.text.lower()


@responses.activate
def test_a_forecast_refresh_persists_rows_and_the_job_records_success(
    mariadb_client: MariaDBClient,
) -> None:
    # End-to-end through the real job registration, not just WeatherRetriever
    # in isolation -- proves the WeatherApiSource adapter and job wrapper are
    # actually wired together correctly, not only that refresh_forecast()
    # itself behaves correctly given a fake source.
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

    retriever = _build_weather_retriever(
        LocationSettings(latitude=51.5, longitude=-0.1), mariadb_client
    )

    scheduler = Scheduler()
    job = register_weather_forecast_refresh_job(scheduler, retriever, mariadb_client)
    job.run().join()

    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).all()
        forecast_rows = (
            session.query(model.weather_forecast)
            .order_by(model.weather_forecast.target_date)
            .all()
        )

    assert len(runs) == 1
    assert runs[0].job_name == "weather_forecast_refresh"
    assert runs[0].status == "success"
    assert len(forecast_rows) == 2
    assert forecast_rows[0].target_date.isoformat() == "2026-09-26"
    assert forecast_rows[1].target_date.isoformat() == "2026-09-27"


@responses.activate
def test_refreshing_the_observation_twice_for_the_same_hour_leaves_one_row(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json={
            "current": {
                "time": "2026-09-25T12:00",
                "temperature_2m": 14.5,
                "relative_humidity_2m": 72,
                "surface_pressure": 1012.3,
                "wind_speed_10m": 8.1,
                "precipitation": 0.0,
            }
        },
        status=200,
    )
    retriever = _build_weather_retriever(
        LocationSettings(latitude=51.5, longitude=-0.1), mariadb_client
    )

    retriever.refresh()
    retriever.refresh()

    with mariadb_client.session_read_scope() as session:
        assert session.query(model.weather_observation).count() == 1
