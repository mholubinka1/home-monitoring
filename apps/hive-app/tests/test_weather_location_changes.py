import logging

import pytest
import responses
from weather_hourly_payloads import recent_hourly_payload

from hive_app.common.config import LocationSettings
from hive_app.data.model import ResolvedLocation
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.main import _build_weather_retriever

OLD_POSTCODE_ENDPOINT = "https://api.postcodes.io/postcodes/AB12CD"
NEW_POSTCODE_ENDPOINT = "https://api.postcodes.io/postcodes/EF34GH"
OPEN_METEO_ENDPOINT = "https://api.open-meteo.com/v1/forecast"

OPEN_METEO_HOURLY_RESPONSE = recent_hourly_payload(1)


def _postcode_response(latitude: float, longitude: float) -> dict:
    return {"status": 200, "result": {"latitude": latitude, "longitude": longitude}}


@responses.activate
def test_a_changed_account_postcode_starts_a_new_weather_series_and_keeps_the_old_one(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        OLD_POSTCODE_ENDPOINT,
        json=_postcode_response(51.4, -0.05),
        status=200,
    )
    responses.add(
        responses.GET,
        NEW_POSTCODE_ENDPOINT,
        json=_postcode_response(53.8, -1.55),
        status=200,
    )
    responses.add(
        responses.GET, OPEN_METEO_ENDPOINT, json=OPEN_METEO_HOURLY_RESPONSE, status=200
    )
    retriever = _build_weather_retriever(None, mariadb_client)
    mariadb_client.write_account_postcode("AB12CD")
    retriever.refresh()

    mariadb_client.write_account_postcode("EF34GH")
    retriever.refresh()

    with mariadb_client.session_read_scope() as session:
        keys = {o.location for o in session.query(model.weather_observation).all()}
    assert keys == {"51.40,-0.05", "53.80,-1.55"}


@responses.activate
def test_adding_an_explicit_location_starts_a_new_weather_series_from_the_next_run(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        OLD_POSTCODE_ENDPOINT,
        json=_postcode_response(51.4, -0.05),
        status=200,
    )
    responses.add(
        responses.GET, OPEN_METEO_ENDPOINT, json=OPEN_METEO_HOURLY_RESPONSE, status=200
    )
    mariadb_client.write_account_postcode("AB12CD")
    _build_weather_retriever(None, mariadb_client).refresh()

    _build_weather_retriever(
        LocationSettings(latitude=50.1, longitude=-1.2), mariadb_client
    ).refresh()

    with mariadb_client.session_read_scope() as session:
        keys = {o.location for o in session.query(model.weather_observation).all()}
    assert keys == {"51.40,-0.05", "50.10,-1.20"}


@responses.activate
def test_changing_an_explicit_location_starts_a_new_weather_series_from_the_next_run(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET, OPEN_METEO_ENDPOINT, json=OPEN_METEO_HOURLY_RESPONSE, status=200
    )
    _build_weather_retriever(
        LocationSettings(latitude=50.1, longitude=-1.2), mariadb_client
    ).refresh()

    _build_weather_retriever(
        LocationSettings(latitude=52.3, longitude=-2.7), mariadb_client
    ).refresh()

    with mariadb_client.session_read_scope() as session:
        keys = {o.location for o in session.query(model.weather_observation).all()}
    assert keys == {"50.10,-1.20", "52.30,-2.70"}


@responses.activate
def test_an_unchanged_account_postcode_reuses_the_cached_location_without_geocoding(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        OLD_POSTCODE_ENDPOINT,
        json=_postcode_response(51.4, -0.05),
        status=200,
    )
    responses.add(
        responses.GET, OPEN_METEO_ENDPOINT, json=OPEN_METEO_HOURLY_RESPONSE, status=200
    )
    mariadb_client.write_account_postcode("AB12CD")
    retriever = _build_weather_retriever(None, mariadb_client)
    retriever.refresh()
    calls_so_far = len(responses.calls)

    retriever.refresh()

    assert [c.request.url.split("?")[0] for c in responses.calls[calls_so_far:]] == [
        OPEN_METEO_ENDPOINT
    ]
    location = mariadb_client.read_weather_location()
    assert location is not None and location.derived_from_postcode == "AB12CD"


@responses.activate
def test_a_changed_postcode_postcodes_io_cannot_locate_keeps_the_cached_location_without_leaking_the_postcode(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    responses.add(
        responses.GET,
        OLD_POSTCODE_ENDPOINT,
        json=_postcode_response(51.4, -0.05),
        status=200,
    )
    responses.add(responses.GET, NEW_POSTCODE_ENDPOINT, status=500)
    responses.add(
        responses.GET, OPEN_METEO_ENDPOINT, json=OPEN_METEO_HOURLY_RESPONSE, status=200
    )
    retriever = _build_weather_retriever(None, mariadb_client)
    mariadb_client.write_account_postcode("AB12CD")
    retriever.refresh()
    mariadb_client.write_account_postcode("EF34GH")

    with caplog.at_level(logging.DEBUG):
        retriever.refresh()

    with mariadb_client.session_read_scope() as session:
        observations = session.query(model.weather_observation).all()
    assert {o.location for o in observations} == {"51.40,-0.05"}
    assert len(observations) >= 1
    assert "postcodes.io could not locate the Account Postcode." in caplog.text
    assert "EF34GH" not in caplog.text
    assert all("EF34GH" not in str(r.exc_info) for r in caplog.records)


@responses.activate
def test_a_cached_postcode_location_from_before_the_postcode_was_recorded_is_rederived_once(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        OLD_POSTCODE_ENDPOINT,
        json=_postcode_response(51.4, -0.05),
        status=200,
    )
    responses.add(
        responses.GET, OPEN_METEO_ENDPOINT, json=OPEN_METEO_HOURLY_RESPONSE, status=200
    )
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    mariadb_client.write_account_postcode("AB12CD")
    retriever = _build_weather_retriever(None, mariadb_client)

    retriever.refresh()
    calls_after_first = len(responses.calls)
    retriever.refresh()

    urls = [c.request.url.split("?")[0] for c in responses.calls]
    assert urls.count(OLD_POSTCODE_ENDPOINT) == 1
    assert urls[calls_after_first:] == [OPEN_METEO_ENDPOINT]
    location = mariadb_client.read_weather_location()
    assert location is not None and location.derived_from_postcode == "AB12CD"
