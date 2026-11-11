from datetime import UTC, datetime, timedelta

import pytest

from octopus_app.data.heating_hour_job import HeatingHourJob
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient, heating_status_table

REGION = "C"
GAS_PRODUCT = "VAR-22-11-01"
GAS_UNIT_RATE_P = 6.0
HOUR = datetime(2026, 11, 2, 18, 0)  # 18:00 UTC; London is on GMT, so also 18:00
NOW = datetime(2026, 11, 2, 20, 0, tzinfo=UTC)
FULL_HOUR_POLLS = 30  # a 2-minute poll interval


def _seed_gas_rate(client: MariaDBClient) -> None:
    valid_from = datetime(2026, 4, 1)
    with client.session_write_scope() as session:
        session.add(
            model.agreement(
                id="G-1",
                energy="G",
                product_code=GAS_PRODUCT,
                tariff_code="G-1R-VAR-22-11-01-C",
                valid_from=valid_from,
                valid_to=None,
            )
        )
        session.add(
            model.product_rate(
                id="G-rate-1",
                product_code=GAS_PRODUCT,
                region=REGION,
                valid_from=valid_from,
                valid_to=None,
                unit_rate=GAS_UNIT_RATE_P,
                standing_charge=29.0,
            )
        )


def _seed_polls(
    client: MariaDBClient,
    hour: datetime,
    *,
    on: int,
    total: int = FULL_HOUR_POLLS,
    boost: bool = False,
) -> None:
    """`total` two-minute polls from the start of `hour`, the first `on` of them
    with the thermostat calling for heat."""
    rows = [
        {
            "polled_at": hour + timedelta(minutes=2 * i),
            "current_temp": 19.0,
            "target_temp": 21.0 if i < on else 7.0,
            "mode": "SCHEDULE",
            "state": "ON" if i < on and not boost else "OFF",
            "boost_active": i < on and boost,
        }
        for i in range(total)
    ]
    with client.session_write_scope() as session:
        session.execute(heating_status_table.insert(), rows)


def _heating_hours(client: MariaDBClient) -> list[model.heating_hour]:
    with client.session_read_scope() as session:
        rows = session.query(model.heating_hour).order_by(model.heating_hour.hour_start)
        return list(rows)


def test_an_hour_the_heating_was_on_is_estimated_and_priced_when_there_is_no_gas_yet(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 12 ON polls (24 minutes) in a completed hour, and no gas data
    _seed_gas_rate(mariadb_client)
    _seed_polls(mariadb_client, HOUR, on=12)

    # When the hourly job runs
    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then the hour is an estimate of 24/60 of an hour at the 12 kW default,
    # priced at the gas unit rate in force
    [row] = _heating_hours(mariadb_client)
    assert row.hour_start == HOUR
    assert row.on_minutes == 24
    assert row.source == "estimated"
    assert row.heating_kwh == pytest.approx(24 / 60 * 12)
    assert row.unit_rate == pytest.approx(GAS_UNIT_RATE_P)
    assert row.cost_gbp == pytest.approx(24 / 60 * 12 * GAS_UNIT_RATE_P / 100)


def test_polls_with_boost_active_count_as_heating_on(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_polls(mariadb_client, HOUR, on=12, boost=True)

    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    [row] = _heating_hours(mariadb_client)
    assert row.on_minutes == 24


def test_an_hour_with_too_few_polls_gets_no_row_and_loses_an_existing_one(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_polls(mariadb_client, HOUR, on=12)
    job = HeatingHourJob(mariadb_client, REGION)
    job.refresh(NOW)
    assert len(_heating_hours(mariadb_client)) == 1

    with mariadb_client.session_write_scope() as session:
        session.execute(heating_status_table.delete())
    _seed_polls(mariadb_client, HOUR, on=12, total=23)
    job.refresh(NOW)

    assert not _heating_hours(mariadb_client)


def test_the_current_unfinished_hour_gets_no_row(
    mariadb_client: MariaDBClient,
) -> None:
    current_hour = NOW.replace(tzinfo=None)
    _seed_polls(mariadb_client, current_hour, on=12)

    HeatingHourJob(mariadb_client, REGION).refresh(NOW + timedelta(minutes=59))

    assert not _heating_hours(mariadb_client)


def test_an_hour_with_no_gas_rate_in_force_is_kept_without_a_rate_or_cost(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_polls(mariadb_client, HOUR, on=12)

    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    [row] = _heating_hours(mariadb_client)
    assert row.heating_kwh == pytest.approx(24 / 60 * 12)
    assert row.unit_rate is None
    assert row.cost_gbp is None


def test_nothing_is_written_when_hive_apps_table_does_not_exist(
    mariadb_client_without_weather_tables: MariaDBClient,
) -> None:
    client = mariadb_client_without_weather_tables

    HeatingHourJob(client, REGION).refresh(NOW)

    assert not _heating_hours(client)
