"""The Room Forecast job: the thermal model run forward from the room's newest
reading to the end of tomorrow, under the Hive schedule and under the Comfort Plan
(ADR-0035).

London is on GMT in November, so local time is UTC."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from octopus_app.data.model import ThermalModelRecord
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import (
    MariaDBClient,
    heating_schedule_table,
    heating_status_table,
    weather_forecast_hour_table,
)
from octopus_app.data.room_forecast_job import RoomForecastJob

# A Tuesday.
NOW = datetime(2026, 11, 17, 14, 10, tzinfo=UTC)
ONE_HOUR = timedelta(hours=1)
LOCATION = "51.50,-0.10"
REGION = "A"
DAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def _usable_model() -> ThermalModelRecord:
    return ThermalModelRecord(
        fitted_at=NOW - timedelta(hours=5),
        window_start=NOW.date() - timedelta(days=60),
        window_end=NOW.date() - timedelta(days=1),
        usable=True,
        unusable_reason=None,
        steps_used=1000,
        heating_steps=200,
        loss_per_degree=0.02,
        heating_gain=1.3,
        carried_gain=0.3,
        sun_gain=0.4,
        constant_gain=0.08,
        backtest_days=10,
        backtest_mean_error=0.2,
        backtest_worst_error=0.6,
    )


def _weekly_schedule(target_on_day: dict[str, float] | None = None) -> dict[str, Any]:
    """Hive's weekly schedule: one slot from midnight a day, at 7 C unless the
    day has its own target."""
    targets = target_on_day or {}
    return {
        day: [{"start": 0, "value": {"target": targets.get(day, 7.0)}}] for day in DAYS
    }


def _seed_reading(
    client: MariaDBClient, at: datetime, room: float, mode: str = "SCHEDULE"
) -> None:
    with client.session_write_scope() as session:
        session.execute(
            heating_status_table.insert(),
            [
                {
                    "polled_at": at.replace(tzinfo=None),
                    "current_temp": room,
                    "target_temp": 7.0,
                    "mode": mode,
                    "state": "OFF",
                    "boost_active": False,
                }
            ],
        )


def _seed_schedule(client: MariaDBClient, schedule: dict[str, Any]) -> None:
    with client.session_write_scope() as session:
        session.execute(
            heating_schedule_table.insert(),
            [{"captured_at": NOW.replace(tzinfo=None), "schedule": schedule}],
        )


def _seed_weather_forecast(
    client: MariaDBClient, first: datetime, last: datetime, outdoor: float = 10.0
) -> None:
    """The hourly forecast for every stamp from `first` to `last` (naive UTC):
    steady outdoor temperature, no sunshine."""
    stamps = []
    stamp = first
    while stamp <= last:
        stamps.append(
            {
                "source": "open-meteo",
                "location": LOCATION,
                "forecast_at": stamp,
                "temp": outdoor,
                "shortwave_radiation": 0.0,
                "fetched_at": NOW.replace(tzinfo=None),
            }
        )
        stamp += ONE_HOUR
    with client.session_write_scope() as session:
        session.execute(weather_forecast_hour_table.insert(), stamps)


def _live_rows(client: MariaDBClient, scenario: str) -> list[Any]:
    rows = model.room_forecast_hour
    with client.session_read_scope() as session:
        return (
            session.query(rows)
            .filter(rows.kind == "live", rows.scenario == scenario)
            .order_by(rows.hour_start)
            .all()
        )


def _seed_gas_rate(client: MariaDBClient, unit_rate_p: float) -> None:
    valid_from = datetime(2026, 4, 1)
    with client.session_write_scope() as session:
        session.add(
            model.agreement(
                id="G-1",
                energy="G",
                product_code="VAR-22-11-01",
                tariff_code="G-1R-VAR-22-11-01-A",
                valid_from=valid_from,
                valid_to=None,
            )
        )
        session.add(
            model.product_rate(
                id="G-rate-1",
                product_code="VAR-22-11-01",
                region=REGION,
                valid_from=valid_from,
                valid_to=None,
                unit_rate=unit_rate_p,
                standing_charge=29.0,
            )
        )


def _seed_measured_hours(client: MariaDBClient, count: int, kw: float) -> None:
    """`count` measured hours in the last month, each on for 30 minutes at `kw`."""
    first = datetime(2026, 11, 10, 10)
    with client.session_write_scope() as session:
        session.add_all(
            model.heating_hour(
                hour_start=first + ONE_HOUR * index,
                poll_count=30,
                on_minutes=30,
                measured_kwh=kw / 2 + 0.3,
                heating_kwh=kw / 2,
                source="measured",
                unit_rate=6.0,
                cost_gbp=0.1,
                computed_at=NOW.replace(tzinfo=None),
            )
            for index in range(count)
        )


@pytest.fixture(name="client")
def _client(mariadb_client: MariaDBClient) -> MariaDBClient:
    mariadb_client.write_thermal_model(_usable_model())
    return mariadb_client


def test_forecast_under_the_current_schedule_runs_from_now_to_the_end_of_tomorrow(
    client: MariaDBClient,
) -> None:
    # Given a usable model, a weekly schedule at 7 C, an hourly weather forecast
    # and a fresh reading of 18 C
    _seed_reading(client, NOW - timedelta(minutes=5), room=18.0)
    _seed_schedule(client, _weekly_schedule())
    _seed_weather_forecast(
        client,
        first=datetime(2026, 11, 17, 14),
        last=datetime(2026, 11, 19, 0),
    )

    # When the job runs
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then live `current` rows run from the hour containing now to the end of
    # tomorrow, starting from the newest reading
    rows = _live_rows(client, "current")
    assert rows[0].hour_start == datetime(2026, 11, 17, 14)
    assert rows[-1].hour_start == datetime(2026, 11, 18, 23)
    assert len(rows) == 34
    assert rows[0].room_temp == pytest.approx(18.0)
    # nothing is heating, so the next hour has cooled by
    # 0.02 * (10 - 18) + 0.08 = -0.08 C
    assert rows[1].room_temp == pytest.approx(17.92)


def test_a_slot_target_above_the_room_is_held_with_the_least_heating(
    client: MariaDBClient,
) -> None:
    # Given Tuesday's slot targets 19 C and the room is at 18 C
    _seed_reading(client, NOW - timedelta(minutes=5), room=18.0)
    _seed_schedule(client, _weekly_schedule({"tuesday": 19.0}))
    _seed_weather_forecast(
        client,
        first=datetime(2026, 11, 17, 14),
        last=datetime(2026, 11, 19, 0),
    )

    # When the job runs
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then the first hour heats for the fraction that just reaches 19 C:
    # (19 - 18 + 0.08) / 1.3 = 0.8308 of the hour, which is 50 minutes
    rows = _live_rows(client, "current")
    assert rows[0].on_minutes == 50
    assert rows[1].room_temp == pytest.approx(19.0)


def _seed_heating_forecast_inputs(client: MariaDBClient) -> None:
    """Tuesday's target is 19 C and the room 18 C, so the first hour heats 50
    minutes."""
    _seed_reading(client, NOW - timedelta(minutes=5), room=18.0)
    _seed_schedule(client, _weekly_schedule({"tuesday": 19.0}))
    _seed_weather_forecast(
        client,
        first=datetime(2026, 11, 17, 14),
        last=datetime(2026, 11, 19, 0),
    )


def test_heating_kwh_and_cost_follow_the_learned_boiler_kw_and_the_gas_rate(
    client: MariaDBClient,
) -> None:
    # Given the boiler has been measured at 6 kW and gas costs 6 p per kWh
    _seed_heating_forecast_inputs(client)
    _seed_measured_hours(client, count=5, kw=6.0)
    _seed_gas_rate(client, unit_rate_p=6.0)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then 50 minutes is 50/60 * 6 = 5 kWh, costing 5 * 6 / 100 = 0.30 pounds
    first = _live_rows(client, "current")[0]
    assert first.heating_kwh == pytest.approx(5.0)
    assert first.cost_gbp == pytest.approx(0.30)


def test_mode_off_forecasts_no_heating(client: MariaDBClient) -> None:
    # Given Hive's mode is OFF, though the schedule asks for 19 C
    _seed_reading(client, NOW - timedelta(minutes=5), room=18.0, mode="OFF")
    _seed_schedule(client, _weekly_schedule({"tuesday": 19.0}))
    _seed_weather_forecast(
        client,
        first=datetime(2026, 11, 17, 14),
        last=datetime(2026, 11, 19, 0),
    )

    # When the job runs
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then no hour heats, and the room cools
    rows = _live_rows(client, "current")
    assert {row.on_minutes for row in rows} == {0}
    assert rows[1].room_temp == pytest.approx(17.92)


@pytest.mark.parametrize(
    "schedule",
    [None, {"monday": "not slots"}, {day: [{"start": "0"}] for day in DAYS}],
    ids=["missing", "not slots", "slot without a target"],
)
def test_an_unreadable_or_missing_schedule_stores_no_current_rows(
    client: MariaDBClient, caplog: pytest.LogCaptureFixture, schedule: Any
) -> None:
    # Given a fresh reading and weather, but no weekly schedule Hive could be
    # read as
    _seed_reading(client, NOW - timedelta(minutes=5), room=18.0)
    if schedule is not None:
        _seed_schedule(client, schedule)
    _seed_weather_forecast(
        client,
        first=datetime(2026, 11, 17, 14),
        last=datetime(2026, 11, 19, 0),
    )

    # When the job runs
    with caplog.at_level("WARNING"):
        RoomForecastJob(client, REGION).refresh(NOW)

    # Then nothing is stored, rather than a forecast of no heating, and a warning
    # says why
    assert _live_rows(client, "current") == []
    assert "schedule" in caplog.text


def test_a_second_run_replaces_the_earlier_live_rows(client: MariaDBClient) -> None:
    # Given a first run from a room of 18 C
    _seed_heating_forecast_inputs(client)
    RoomForecastJob(client, REGION).refresh(NOW)

    # When a fresher reading of 20 C arrives and the job runs again
    later = NOW + timedelta(minutes=10)
    _seed_reading(client, later - timedelta(minutes=1), room=20.0)
    RoomForecastJob(client, REGION).refresh(later)

    # Then only the second run's rows are stored
    rows = _live_rows(client, "current")
    assert len(rows) == 34
    assert rows[0].room_temp == pytest.approx(20.0)


def test_cost_is_empty_without_a_gas_rate(client: MariaDBClient) -> None:
    # Given no gas rate and no measured hours, so the default 12 kW boiler
    _seed_heating_forecast_inputs(client)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then the energy is estimated and there is no cost
    first = _live_rows(client, "current")[0]
    assert first.heating_kwh == pytest.approx(10.0)
    assert first.cost_gbp is None
