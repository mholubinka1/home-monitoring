"""The Room Forecast job: the thermal model run forward from the room's newest
reading to the end of tomorrow, under the Hive schedule and under the Comfort Plan
(ADR-0035).

London is on GMT in November, so local time is UTC."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from common.local_day import set_local_timezone
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


def test_a_reading_more_than_30_minutes_old_stores_nothing(
    client: MariaDBClient,
) -> None:
    # Given a forecast stored from a fresh reading at 14:05
    _seed_heating_forecast_inputs(client)
    RoomForecastJob(client, REGION).refresh(NOW)

    # When the job runs an hour later and no newer reading has arrived, so the
    # newest is 65 minutes old
    RoomForecastJob(client, REGION).refresh(NOW + ONE_HOUR)

    # Then nothing new is stored and the earlier rows are left as they were
    rows = _live_rows(client, "current")
    assert rows[0].hour_start == datetime(2026, 11, 17, 14)
    assert len(rows) == 34


def test_a_reading_exactly_30_minutes_old_is_fresh(client: MariaDBClient) -> None:
    # Given the newest reading is exactly 30 minutes old
    _seed_reading(client, NOW - timedelta(minutes=30), room=18.0)
    _seed_schedule(client, _weekly_schedule())
    _seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 14), last=datetime(2026, 11, 19, 0)
    )

    # When the job runs
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then it counts as fresh and the forecast is stored
    assert len(_live_rows(client, "current")) == 34


def test_an_unusable_newest_model_stores_nothing_and_never_falls_back(
    client: MariaDBClient,
) -> None:
    # Given a forecast stored under the usable model, then a newer model that is
    # not usable
    _seed_heating_forecast_inputs(client)
    RoomForecastJob(client, REGION).refresh(NOW)
    unusable = replace(
        _usable_model(),
        fitted_at=NOW - timedelta(hours=1),
        usable=False,
        unusable_reason="too few heating steps",
    )
    client.write_thermal_model(unusable)
    _seed_reading(client, NOW + timedelta(minutes=9), room=21.0)

    # When the job runs again
    RoomForecastJob(client, REGION).refresh(NOW + timedelta(minutes=10))

    # Then the older usable model is not used and the earlier rows are as they were
    rows = _live_rows(client, "current")
    assert len(rows) == 34
    assert rows[0].room_temp == pytest.approx(18.0)


def test_no_model_at_all_stores_nothing(mariadb_client: MariaDBClient) -> None:
    # Given a fresh reading, a schedule and weather, but no thermal model
    _seed_heating_forecast_inputs(mariadb_client)

    # When the job runs
    RoomForecastJob(mariadb_client, REGION).refresh(NOW)

    # Then nothing is stored
    assert _live_rows(mariadb_client, "current") == []


@pytest.mark.parametrize(
    "later",
    [
        datetime(2026, 11, 19, 0, 10, tzinfo=UTC),
        datetime(2026, 11, 19, 1, 10, tzinfo=UTC),
    ],
    ids=["no radiation stamp for the first hour", "no stamp for the first hour"],
)
def test_no_weather_for_the_first_hour_leaves_the_earlier_rows(
    client: MariaDBClient, later: datetime
) -> None:
    # Given a forecast stored earlier, and weather that ends at 00:00 on the 19th
    _seed_heating_forecast_inputs(client)
    RoomForecastJob(client, REGION).refresh(NOW)
    _seed_reading(client, later - timedelta(minutes=1), room=20.0)

    # When the job runs at a time whose first hour (or its radiation) has no weather
    RoomForecastJob(client, REGION).refresh(later)

    # Then the earlier rows are not deleted or replaced
    rows = _live_rows(client, "current")
    assert len(rows) == 34
    assert rows[0].room_temp == pytest.approx(18.0)


def test_a_weather_gap_later_in_the_horizon_stops_the_forecast_there(
    client: MariaDBClient,
) -> None:
    # Given a forecast stored earlier, then weather only to 20:00 for the next run
    _seed_heating_forecast_inputs(client)
    RoomForecastJob(client, REGION).refresh(NOW)
    _seed_reading(client, NOW + timedelta(minutes=9), room=20.0)
    with client.session_write_scope() as session:
        session.execute(
            weather_forecast_hour_table.delete().where(
                weather_forecast_hour_table.c.forecast_at > datetime(2026, 11, 17, 20)
            )
        )

    # When the job runs again
    RoomForecastJob(client, REGION).refresh(NOW + timedelta(minutes=10))

    # Then the rows run up to the last hour whose radiation is known, replacing
    # the earlier ones
    rows = _live_rows(client, "current")
    assert rows[-1].hour_start == datetime(2026, 11, 17, 19)
    assert len(rows) == 6
    assert rows[0].room_temp == pytest.approx(20.0)


def _seed_weather_stamps(
    client: MariaDBClient, stamps: list[tuple[datetime, float, float | None]]
) -> None:
    """(stamp, outdoor temperature, shortwave radiation) rows of the forecast."""
    with client.session_write_scope() as session:
        session.execute(
            weather_forecast_hour_table.insert(),
            [
                {
                    "source": "open-meteo",
                    "location": LOCATION,
                    "forecast_at": stamp,
                    "temp": temp,
                    "shortwave_radiation": radiation,
                    "fetched_at": NOW.replace(tzinfo=None),
                }
                for stamp, temp, radiation in stamps
            ],
        )


def test_an_unknown_radiation_counts_as_no_sunshine_and_keeps_the_hour(
    client: MariaDBClient,
) -> None:
    # Given a forecast whose radiation is unknown
    _seed_reading(client, NOW - timedelta(minutes=5), room=18.0)
    _seed_schedule(client, _weekly_schedule())
    _seed_weather_stamps(
        client,
        [
            (datetime(2026, 11, 17, 14) + ONE_HOUR * index, 10.0, None)
            for index in range(36)
        ],
    )

    # When the job runs
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then every hour is kept and the room cools as with no sunshine
    rows = _live_rows(client, "current")
    assert len(rows) == 34
    assert rows[1].room_temp == pytest.approx(17.92)


def test_an_hours_temperature_pairs_with_the_next_stamps_radiation(
    client: MariaDBClient,
) -> None:
    # Given 10 C and no sunshine at 14:00, and 14 C and 1000 W/m2 at 15:00
    _seed_reading(client, NOW - timedelta(minutes=5), room=18.0)
    _seed_schedule(client, _weekly_schedule())
    _seed_weather_stamps(
        client,
        [
            (datetime(2026, 11, 17, 14), 10.0, 0.0),
            (datetime(2026, 11, 17, 15), 14.0, 1000.0),
        ]
        + [
            (datetime(2026, 11, 17, 16) + ONE_HOUR * index, 10.0, 0.0)
            for index in range(34)
        ],
    )

    # When the job runs
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then 14:00 uses its own 10 C with 15:00's 1 kW/m2:
    # 0.02 * (10 - 18) + 0.4 * 1 + 0.08 = 0.32 C
    rows = _live_rows(client, "current")
    assert rows[1].room_temp == pytest.approx(18.32)


@pytest.mark.parametrize(
    ("now", "rows_stored", "last_hour"),
    [
        # tomorrow, Sunday 25 October, has 25 hours (clocks go back)
        (datetime(2026, 10, 24, 14, 10, tzinfo=UTC), 34, datetime(2026, 10, 25, 23)),
        # tomorrow, Sunday 29 March, has 23 hours (clocks go forward)
        (datetime(2026, 3, 28, 14, 10, tzinfo=UTC), 33, datetime(2026, 3, 29, 22)),
    ],
    ids=["25-hour day", "23-hour day"],
)
def test_the_horizon_ends_at_the_last_hour_of_tomorrows_local_day(
    client: MariaDBClient, now: datetime, rows_stored: int, last_hour: datetime
) -> None:
    # Given London time and the day before a clock change
    set_local_timezone("Europe/London")
    first = now.replace(minute=0, tzinfo=None)
    _seed_reading(client, now - timedelta(minutes=5), room=18.0)
    _seed_schedule(client, _weekly_schedule())
    _seed_weather_forecast(client, first=first, last=first + ONE_HOUR * 40)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(now)

    # Then the rows end at the last hour of tomorrow's local day
    rows = _live_rows(client, "current")
    assert rows[-1].hour_start == last_hour
    assert len(rows) == rows_stored


def test_missing_hive_tables_store_nothing_and_do_not_raise(
    mariadb_client_without_weather_tables: MariaDBClient,
) -> None:
    # Given hive-app has never run, so its tables do not exist
    mariadb_client_without_weather_tables.write_thermal_model(_usable_model())

    # When the job runs
    RoomForecastJob(mariadb_client_without_weather_tables, REGION).refresh(NOW)

    # Then nothing is stored
    assert _live_rows(mariadb_client_without_weather_tables, "current") == []
