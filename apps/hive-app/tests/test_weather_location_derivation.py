import logging
from typing import Any

import pytest
import responses
from schedule import Scheduler

from hive_app.common.config import LocationSettings
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.weather_location import WeatherLocationUnavailableError
from hive_app.main import _build_weather_retriever, register_weather_jobs

POSTCODES_IO_ENDPOINT = "https://api.postcodes.io/postcodes/AB12CD"
IPWHO_ENDPOINT = "https://ipwho.is/"
OPEN_METEO_ENDPOINT = "https://api.open-meteo.com/v1/forecast"

OPEN_METEO_CURRENT_RESPONSE = {
    "current": {
        "time": "2026-09-25T12:00",
        "temperature_2m": 14.5,
        "relative_humidity_2m": 72,
        "surface_pressure": 1012.3,
        "wind_speed_10m": 8.1,
        "precipitation": 0.0,
    }
}


@responses.activate
def test_with_no_weather_config_an_account_postcode_locates_the_observation_and_is_cached(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_account_postcode("AB12CD")
    responses.add(
        responses.GET,
        POSTCODES_IO_ENDPOINT,
        json={"status": 200, "result": {"latitude": 51.4, "longitude": -0.05}},
        status=200,
    )
    responses.add(
        responses.GET, OPEN_METEO_ENDPOINT, json=OPEN_METEO_CURRENT_RESPONSE, status=200
    )

    retriever = _build_weather_retriever(None, None, mariadb_client)
    assert retriever is not None
    retriever.refresh()

    open_meteo_request = responses.calls[-1].request
    assert "latitude=51.4" in open_meteo_request.url
    assert "longitude=-0.05" in open_meteo_request.url
    with mariadb_client.session_read_scope() as session:
        observations = session.query(model.weather_observation).all()
        locations = session.query(model.weather_location).all()
    assert [o.source for o in observations] == ["open-meteo"]
    assert [(loc.latitude, loc.longitude, loc.source) for loc in locations] == [
        (51.4, -0.05, "postcode")
    ]


@responses.activate
def test_a_cached_location_is_used_without_geocoding_again(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_account_postcode("AB12CD")
    responses.add(
        responses.GET,
        POSTCODES_IO_ENDPOINT,
        json={"status": 200, "result": {"latitude": 51.4, "longitude": -0.05}},
        status=200,
    )
    responses.add(
        responses.GET, OPEN_METEO_ENDPOINT, json=OPEN_METEO_CURRENT_RESPONSE, status=200
    )
    retriever = _build_weather_retriever(None, None, mariadb_client)
    retriever.refresh()
    calls_so_far = len(responses.calls)

    retriever.refresh()

    second_refresh_calls = responses.calls[calls_so_far:]
    assert [call.request.url.split("?")[0] for call in second_refresh_calls] == [
        OPEN_METEO_ENDPOINT
    ]


@responses.activate
def test_with_no_account_postcode_ip_geolocation_locates_the_observation_and_is_cached(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        IPWHO_ENDPOINT,
        json={"success": True, "latitude": 53.4, "longitude": -2.2},
        status=200,
    )
    responses.add(
        responses.GET, OPEN_METEO_ENDPOINT, json=OPEN_METEO_CURRENT_RESPONSE, status=200
    )

    _build_weather_retriever(None, None, mariadb_client).refresh()

    open_meteo_request = responses.calls[-1].request
    assert "latitude=53.4" in open_meteo_request.url
    assert "longitude=-2.2" in open_meteo_request.url
    with mariadb_client.session_read_scope() as session:
        locations = session.query(model.weather_location).all()
    assert [(loc.latitude, loc.longitude, loc.source) for loc in locations] == [
        (53.4, -2.2, "ip")
    ]


@responses.activate
def test_an_explicit_location_config_is_used_and_never_cached(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_account_postcode("AB12CD")
    responses.add(
        responses.GET, OPEN_METEO_ENDPOINT, json=OPEN_METEO_CURRENT_RESPONSE, status=200
    )

    _build_weather_retriever(
        None, LocationSettings(latitude=50.1, longitude=-1.2), mariadb_client
    ).refresh()

    assert len(responses.calls) == 1
    assert "latitude=50.1" in responses.calls[0].request.url
    assert "longitude=-1.2" in responses.calls[0].request.url
    with mariadb_client.session_read_scope() as session:
        assert session.query(model.weather_location).count() == 0


@responses.activate
def test_when_no_location_resolves_the_run_fails_clearly_and_a_later_run_recovers(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    responses.add(responses.GET, IPWHO_ENDPOINT, json={"success": False}, status=200)
    retriever = _build_weather_retriever(None, None, mariadb_client)

    with pytest.raises(WeatherLocationUnavailableError):
        retriever.refresh()

    assert "Weather Location" in caplog.text
    with mariadb_client.session_read_scope() as session:
        assert session.query(model.weather_location).count() == 0
        assert session.query(model.weather_observation).count() == 0

    responses.replace(
        responses.GET,
        IPWHO_ENDPOINT,
        json={"success": True, "latitude": 53.4, "longitude": -2.2},
        status=200,
    )
    responses.add(
        responses.GET, OPEN_METEO_ENDPOINT, json=OPEN_METEO_CURRENT_RESPONSE, status=200
    )

    retriever.refresh()

    with mariadb_client.session_read_scope() as session:
        assert session.query(model.weather_observation).count() == 1


@responses.activate
def test_with_no_weather_config_both_weather_jobs_register_and_the_forecast_uses_the_derived_location(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_account_postcode("AB12CD")
    responses.add(
        responses.GET,
        POSTCODES_IO_ENDPOINT,
        json={"status": 200, "result": {"latitude": 51.4, "longitude": -0.05}},
        status=200,
    )
    responses.add(
        responses.GET, OPEN_METEO_ENDPOINT, json=OPEN_METEO_CURRENT_RESPONSE, status=200
    )
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
    scheduler = Scheduler()

    observation_job, forecast_job = register_weather_jobs(
        scheduler, _build_weather_retriever(None, None, mariadb_client), mariadb_client
    )
    observation_job.run().join()
    forecast_job.run().join()

    assert len(scheduler.get_jobs()) == 2
    assert "latitude=51.4" in responses.calls[-1].request.url
    with mariadb_client.session_read_scope() as session:
        assert session.query(model.weather_observation).count() == 1
        assert session.query(model.weather_forecast).count() == 2
        assert {run.status for run in session.query(model.job_run).all()} == {"success"}


@responses.activate
def test_deriving_the_location_logs_its_source_and_coordinates(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    mariadb_client.write_account_postcode("AB12CD")
    responses.add(
        responses.GET,
        POSTCODES_IO_ENDPOINT,
        json={"status": 200, "result": {"latitude": 51.4, "longitude": -0.05}},
        status=200,
    )
    responses.add(
        responses.GET, OPEN_METEO_ENDPOINT, json=OPEN_METEO_CURRENT_RESPONSE, status=200
    )

    with caplog.at_level(logging.INFO):
        _build_weather_retriever(None, None, mariadb_client).refresh()

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("postcode" in m and "51.4" in m and "-0.05" in m for m in messages)


@pytest.mark.parametrize(
    "postcodes_io_reply",
    [
        {"status": 500},
        {"status": 404, "json": {"status": 404, "error": "Invalid postcode"}},
        {"status": 200, "json": {"status": 200, "result": None}},
    ],
)
@responses.activate
def test_when_postcodes_io_cannot_locate_the_postcode_ip_geolocation_is_used(
    mariadb_client: MariaDBClient, postcodes_io_reply: dict[str, Any]
) -> None:
    mariadb_client.write_account_postcode("AB12CD")
    responses.add(responses.GET, POSTCODES_IO_ENDPOINT, **postcodes_io_reply)
    responses.add(
        responses.GET,
        IPWHO_ENDPOINT,
        json={"success": True, "latitude": 53.4, "longitude": -2.2},
        status=200,
    )
    responses.add(
        responses.GET, OPEN_METEO_ENDPOINT, json=OPEN_METEO_CURRENT_RESPONSE, status=200
    )

    _build_weather_retriever(None, None, mariadb_client).refresh()

    assert "latitude=53.4" in responses.calls[-1].request.url
    with mariadb_client.session_read_scope() as session:
        locations = session.query(model.weather_location).all()
    assert [loc.source for loc in locations] == ["ip"]
