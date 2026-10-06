import json

import pytest
import responses
from requests import PreparedRequest
from responses import matchers

from hive_app.common.config import LocationSettings, WeatherUndergroundSettings
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.main import _build_weather_retriever

POSTCODES_IO_ENDPOINT = "https://api.postcodes.io/postcodes/AB12CD"
WU_NEARBY_ENDPOINT = "https://api.weather.com/v3/location/near"
WU_ENDPOINT = "https://api.weather.com/v2/pws/observations/current"

WU_OBSERVATION_RESPONSE = {
    "observations": [
        {
            "obsTimeUtc": "2026-09-25T12:00:00Z",
            "humidity": 72,
            "metric": {
                "temp": 14.5,
                "pressure": 1012.3,
                "windSpeed": 8.1,
                "precipTotal": 0.0,
            },
        }
    ]
}


def _nearby_stations(*station_ids: str) -> dict[str, object]:
    # Nearest first, as Weather Underground returns them.
    return {
        "location": {
            "stationId": list(station_ids),
            "distanceKm": [0.4 * (i + 1) for i in range(len(station_ids))],
        }
    }


@responses.activate
def test_with_only_an_api_key_the_nearest_reporting_station_is_used_and_cached(
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
        responses.GET,
        WU_NEARBY_ENDPOINT,
        json=_nearby_stations("ISTATIONA", "ISTATIONB"),
        status=200,
        match=[
            matchers.query_param_matcher({"geocode": "51.4,-0.05"}, strict_match=False)
        ],
    )
    responses.add(
        responses.GET,
        WU_ENDPOINT,
        json=WU_OBSERVATION_RESPONSE,
        status=200,
        match=[
            matchers.query_param_matcher({"stationId": "ISTATIONA"}, strict_match=False)
        ],
    )

    retriever = _build_weather_retriever(
        WeatherUndergroundSettings(api_key="test-key"), None, mariadb_client
    )
    retriever.refresh()

    with mariadb_client.session_read_scope() as session:
        observations = session.query(model.weather_observation).all()
        locations = session.query(model.weather_location).all()
    assert [o.source for o in observations] == ["wunderground"]
    assert [loc.station_id for loc in locations] == ["ISTATIONA"]


def _stub_location_and_nearby(*station_ids: str) -> None:
    responses.add(
        responses.GET,
        POSTCODES_IO_ENDPOINT,
        json={"status": 200, "result": {"latitude": 51.4, "longitude": -0.05}},
        status=200,
    )
    responses.add(
        responses.GET,
        WU_NEARBY_ENDPOINT,
        json=_nearby_stations(*station_ids),
        status=200,
    )


def _stub_observation(station_id: str, reporting: bool = True) -> None:
    responses.add(
        responses.GET,
        WU_ENDPOINT,
        json=WU_OBSERVATION_RESPONSE if reporting else {"observations": []},
        status=200,
        match=[
            matchers.query_param_matcher({"stationId": station_id}, strict_match=False)
        ],
    )


@responses.activate
def test_a_nearest_station_that_is_not_reporting_is_skipped_for_the_next_one(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_account_postcode("AB12CD")
    _stub_location_and_nearby("ISTATIONA", "ISTATIONB")
    _stub_observation("ISTATIONA", reporting=False)
    _stub_observation("ISTATIONB")

    retriever = _build_weather_retriever(
        WeatherUndergroundSettings(api_key="test-key"), None, mariadb_client
    )
    retriever.refresh()

    with mariadb_client.session_read_scope() as session:
        observations = session.query(model.weather_observation).all()
        locations = session.query(model.weather_location).all()
    assert [o.source for o in observations] == ["wunderground"]
    assert [loc.station_id for loc in locations] == ["ISTATIONB"]


def _calls_to(endpoint: str) -> int:
    return sum(1 for call in responses.calls if call.request.url.startswith(endpoint))


@responses.activate
def test_a_cached_station_is_used_without_another_nearby_stations_lookup(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_account_postcode("AB12CD")
    _stub_location_and_nearby("ISTATIONA", "ISTATIONB")
    _stub_observation("ISTATIONA")
    retriever = _build_weather_retriever(
        WeatherUndergroundSettings(api_key="test-key"), None, mariadb_client
    )

    retriever.refresh()
    retriever.refresh()

    assert _calls_to(WU_NEARBY_ENDPOINT) == 1
    assert _calls_to(WU_ENDPOINT) == 2


OPEN_METEO_ENDPOINT = "https://api.open-meteo.com/v1/forecast"


def _stub_open_meteo() -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json={
            "current": {
                "time": "2026-09-25T12:30",
                "temperature_2m": 14.5,
                "relative_humidity_2m": 72,
                "surface_pressure": 1012.3,
                "wind_speed_10m": 8.1,
                "precipitation": 0.0,
            }
        },
        status=200,
    )


def _cached_station(mariadb_client: MariaDBClient) -> str | None:
    with mariadb_client.session_read_scope() as session:
        return session.query(model.weather_location).one().station_id


def _observation_sources(mariadb_client: MariaDBClient) -> set[str]:
    with mariadb_client.session_read_scope() as session:
        return {o.source for o in session.query(model.weather_observation)}


@responses.activate
def test_a_cached_station_that_stops_reporting_is_replaced_on_the_next_run(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_account_postcode("AB12CD")
    _stub_location_and_nearby("ISTATIONA", "ISTATIONB")
    _stub_open_meteo()
    station_a_reporting = True

    def station_a(_request: PreparedRequest) -> tuple[int, dict[str, str], str]:
        body = WU_OBSERVATION_RESPONSE if station_a_reporting else {"observations": []}
        return 200, {}, json.dumps(body)

    responses.add_callback(  # pylint: disable=unexpected-keyword-arg
        responses.GET,
        WU_ENDPOINT,
        callback=station_a,
        match=[
            matchers.query_param_matcher({"stationId": "ISTATIONA"}, strict_match=False)
        ],
    )
    _stub_observation("ISTATIONB")
    retriever = _build_weather_retriever(
        WeatherUndergroundSettings(api_key="test-key"), None, mariadb_client
    )
    retriever.refresh()
    assert _cached_station(mariadb_client) == "ISTATIONA"

    station_a_reporting = False
    retriever.refresh()
    assert "open-meteo" in _observation_sources(mariadb_client)
    assert _cached_station(mariadb_client) is None

    retriever.refresh()
    assert _cached_station(mariadb_client) == "ISTATIONB"


@responses.activate
def test_a_transient_failure_reading_the_cached_station_keeps_it_for_the_next_run(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_account_postcode("AB12CD")
    _stub_location_and_nearby("ISTATIONA", "ISTATIONB")
    _stub_open_meteo()
    station_a_failing = False

    def station_a(_request: PreparedRequest) -> tuple[int, dict[str, str], str]:
        if station_a_failing:
            return 500, {}, ""
        return 200, {}, json.dumps(WU_OBSERVATION_RESPONSE)

    responses.add_callback(  # pylint: disable=unexpected-keyword-arg
        responses.GET,
        WU_ENDPOINT,
        callback=station_a,
        match=[
            matchers.query_param_matcher({"stationId": "ISTATIONA"}, strict_match=False)
        ],
    )
    retriever = _build_weather_retriever(
        WeatherUndergroundSettings(api_key="test-key"), None, mariadb_client
    )
    retriever.refresh()

    station_a_failing = True
    retriever.refresh()
    assert "open-meteo" in _observation_sources(mariadb_client)
    assert _cached_station(mariadb_client) == "ISTATIONA"

    station_a_failing = False
    wu_calls_before = _calls_to(WU_ENDPOINT)
    retriever.refresh()
    assert _calls_to(WU_ENDPOINT) == wu_calls_before + 1
    assert _calls_to(WU_NEARBY_ENDPOINT) == 1
    assert _cached_station(mariadb_client) == "ISTATIONA"


@responses.activate
def test_with_no_reporting_station_nearby_observations_come_from_open_meteo(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_account_postcode("AB12CD")
    _stub_location_and_nearby("ISTATIONA", "ISTATIONB")
    _stub_open_meteo()
    _stub_observation("ISTATIONA", reporting=False)
    _stub_observation("ISTATIONB", reporting=False)

    retriever = _build_weather_retriever(
        WeatherUndergroundSettings(api_key="test-key"), None, mariadb_client
    )
    retriever.refresh()

    assert _observation_sources(mariadb_client) == {"open-meteo"}
    assert _cached_station(mariadb_client) is None


@responses.activate
def test_only_the_five_nearest_stations_are_tried_for_a_reading(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_account_postcode("AB12CD")
    stations = [f"ISTATION{n}" for n in range(6)]
    _stub_location_and_nearby(*stations)
    _stub_open_meteo()
    for station in stations[:5]:
        _stub_observation(station, reporting=False)
    _stub_observation(stations[5])

    retriever = _build_weather_retriever(
        WeatherUndergroundSettings(api_key="test-key"), None, mariadb_client
    )
    retriever.refresh()

    assert _calls_to(WU_ENDPOINT) == 5
    assert _observation_sources(mariadb_client) == {"open-meteo"}


@responses.activate
def test_an_explicit_station_is_used_without_a_nearby_lookup_or_caching(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_account_postcode("AB12CD")
    _stub_location_and_nearby("ISTATIONA")
    _stub_observation("IEXPLICIT")

    retriever = _build_weather_retriever(
        WeatherUndergroundSettings(api_key="test-key", station_id="IEXPLICIT"),
        None,
        mariadb_client,
    )
    retriever.refresh()

    assert _calls_to(WU_NEARBY_ENDPOINT) == 0
    assert _observation_sources(mariadb_client) == {"wunderground"}
    with mariadb_client.session_read_scope() as session:
        assert [loc.station_id for loc in session.query(model.weather_location)] == []


@responses.activate
def test_with_a_configured_location_the_station_is_discovered_each_run_without_caching(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        WU_NEARBY_ENDPOINT,
        json=_nearby_stations("ISTATIONA"),
        status=200,
        match=[
            matchers.query_param_matcher({"geocode": "51.5,-0.1"}, strict_match=False)
        ],
    )
    _stub_observation("ISTATIONA")
    retriever = _build_weather_retriever(
        WeatherUndergroundSettings(api_key="test-key"),
        LocationSettings(latitude=51.5, longitude=-0.1),
        mariadb_client,
    )

    retriever.refresh()
    retriever.refresh()

    assert _calls_to(WU_NEARBY_ENDPOINT) == 2
    with mariadb_client.session_read_scope() as session:
        assert session.query(model.weather_location).count() == 0


@responses.activate
def test_a_failed_nearby_stations_lookup_falls_back_to_open_meteo_without_leaking_the_api_key(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    mariadb_client.write_account_postcode("AB12CD")
    responses.add(
        responses.GET,
        POSTCODES_IO_ENDPOINT,
        json={"status": 200, "result": {"latitude": 51.4, "longitude": -0.05}},
        status=200,
    )
    responses.add(responses.GET, WU_NEARBY_ENDPOINT, status=500)
    _stub_open_meteo()

    retriever = _build_weather_retriever(
        WeatherUndergroundSettings(api_key="secret-key-123"), None, mariadb_client
    )
    retriever.refresh()

    assert _observation_sources(mariadb_client) == {"open-meteo"}
    assert _cached_station(mariadb_client) is None
    assert "secret-key-123" not in caplog.text
