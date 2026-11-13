"""The hourly heating chain: the heating hours, then the jobs that read them."""

from unittest.mock import Mock

import pytest
from schedule import Scheduler

from octopus_app.data.comfort_job import ComfortDayJob
from octopus_app.data.heating_hour_job import HeatingHourJob
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.thermal_job import ThermalModelJob
from octopus_app.main import register_heating_hour_job


def _runs_of(mariadb_client: MariaDBClient, job_name: str) -> list[str]:
    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).filter_by(job_name=job_name).all()
    return [run.status for run in runs]


def test_heating_hour_job_is_registered_hourly(
    mariadb_client: MariaDBClient,
) -> None:
    job = register_heating_hour_job(
        Scheduler(),
        Mock(spec=HeatingHourJob),
        Mock(spec=ComfortDayJob),
        Mock(spec=ThermalModelJob),
        mariadb_client,
    )

    assert job.interval == 1
    assert job.unit == "hours"


def test_the_comfort_days_then_the_thermal_model_follow_a_successful_heating_hour_run(
    mariadb_client: MariaDBClient,
) -> None:
    steps = Mock()
    heating_hour = Mock(spec=HeatingHourJob)
    comfort_day = Mock(spec=ComfortDayJob)
    thermal_model = Mock(spec=ThermalModelJob)
    steps.attach_mock(heating_hour.refresh, "heating_hour")
    steps.attach_mock(comfort_day.refresh, "comfort_day")
    steps.attach_mock(thermal_model.refresh, "thermal_model")

    register_heating_hour_job(
        Scheduler(), heating_hour, comfort_day, thermal_model, mariadb_client
    ).run().join()

    assert [name for name, _, _ in steps.mock_calls] == [
        "heating_hour",
        "comfort_day",
        "thermal_model",
    ]
    assert _runs_of(mariadb_client, "heating_hour_refresh") == ["success"]
    assert _runs_of(mariadb_client, "comfort_day_refresh") == ["success"]
    assert _runs_of(mariadb_client, "thermal_model_refresh") == ["success"]


def test_a_failing_heating_hour_run_retries_and_skips_the_comfort_days_and_thermal_model(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("octopus_app.common.decorator.time.sleep", lambda seconds: None)
    heating_hour = Mock(spec=HeatingHourJob)
    heating_hour.refresh.side_effect = RuntimeError("db down")
    comfort_day = Mock(spec=ComfortDayJob)
    thermal_model = Mock(spec=ThermalModelJob)

    register_heating_hour_job(
        Scheduler(), heating_hour, comfort_day, thermal_model, mariadb_client
    ).run().join()

    heating_hour_runs = _runs_of(mariadb_client, "heating_hour_refresh")
    assert len(heating_hour_runs) > 1
    assert set(heating_hour_runs) == {"failure"}
    comfort_day.refresh.assert_not_called()
    thermal_model.refresh.assert_not_called()
    assert _runs_of(mariadb_client, "comfort_day_refresh") == ["skipped"]
    assert _runs_of(mariadb_client, "thermal_model_refresh") == ["skipped"]


def test_a_failing_comfort_day_refresh_does_not_stop_the_thermal_model(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("octopus_app.common.decorator.time.sleep", lambda seconds: None)
    comfort_day = Mock(spec=ComfortDayJob)
    comfort_day.refresh.side_effect = RuntimeError("comfort exploded")
    thermal_model = Mock(spec=ThermalModelJob)

    register_heating_hour_job(
        Scheduler(),
        Mock(spec=HeatingHourJob),
        comfort_day,
        thermal_model,
        mariadb_client,
    ).run().join()

    assert set(_runs_of(mariadb_client, "comfort_day_refresh")) == {"failure"}
    thermal_model.refresh.assert_called_once()
    assert _runs_of(mariadb_client, "thermal_model_refresh") == ["success"]
