from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from schedule import Scheduler

from hive_app.data.model import WeatherForecastDay, WeatherObservation
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.weather import WeatherRetriever
from hive_app.main import register_weather_observation_refresh_job


def test_weather_observation_refresh_job_runs_on_a_60_minute_interval(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()

    job = register_weather_observation_refresh_job(
        scheduler, Mock(spec=WeatherRetriever), mariadb_client
    )

    assert job.interval == 60
    assert job.unit == "minutes"


def test_a_successful_weather_observation_refresh_is_recorded_as_a_successful_job_run(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()
    weather = Mock(spec=WeatherRetriever)

    job = register_weather_observation_refresh_job(scheduler, weather, mariadb_client)
    job.run().join()

    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).all()

    assert len(runs) == 1
    assert runs[0].job_name == "weather_observation_refresh"
    assert runs[0].status == "success"
    assert runs[0].error_message is None


def test_a_persistently_failing_weather_observation_refresh_retries_with_backoff(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleep_delays: list[int] = []
    monkeypatch.setattr("hive_app.common.decorator.time.sleep", sleep_delays.append)
    scheduler = Scheduler()
    weather = Mock(spec=WeatherRetriever)
    weather.refresh.side_effect = RuntimeError("api.open-meteo.com unreachable")

    job = register_weather_observation_refresh_job(scheduler, weather, mariadb_client)
    job.run().join()

    assert sleep_delays == [60, 120, 240, 480]
    assert weather.refresh.call_count == 5

    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).all()

    assert len(runs) == 5
    assert all(run.status == "failure" for run in runs)
    assert all(run.error_message == "api.open-meteo.com unreachable" for run in runs)


class _OpenMeteoSource:
    """A fake WeatherSource whose observation fetch succeeds -- used (unlike
    the Mock-based tests above) to prove observation persistence is wired
    end-to-end through job registration into a recorded job_run success."""

    def __init__(self, mariadb: MariaDBClient) -> None:
        self._mariadb = mariadb

    def fetch_recent_observations(self) -> list[WeatherObservation]:
        return [
            WeatherObservation(
                source="open-meteo",
                location="51.50,-0.10",
                observed_at=datetime(2026, 9, 25, hour, 0, tzinfo=UTC),
                temp=14.5,
                humidity=72,
                pressure=1012.3,
                wind_speed=8.1,
                precipitation=0.0,
            )
            for hour in (11, 12)
        ]

    def persist_observations(self, observations: list[WeatherObservation]) -> None:
        self._mariadb.write_weather_observations(observations)

    def fetch_forecast(self) -> list[WeatherForecastDay]:
        raise NotImplementedError

    def persist_forecast(self, forecast: list[WeatherForecastDay]) -> None:
        raise NotImplementedError


def test_an_observation_refresh_persists_and_the_job_records_success(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()
    weather = WeatherRetriever(_OpenMeteoSource(mariadb_client))

    job = register_weather_observation_refresh_job(scheduler, weather, mariadb_client)
    job.run().join()

    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).all()
        observations = session.query(model.weather_observation).all()

    assert len(runs) == 1
    assert runs[0].job_name == "weather_observation_refresh"
    assert runs[0].status == "success"
    assert len(observations) == 2
    assert observations[0].source == "open-meteo"
