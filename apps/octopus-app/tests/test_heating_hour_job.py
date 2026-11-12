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


def _seed_gas(client: MariaDBClient, hour: datetime, kwh: float) -> None:
    """Two half-hourly gas readings in `hour` that together read `kwh`."""
    with client.session_write_scope() as session:
        for half in range(2):
            period_from = hour + timedelta(minutes=30 * half)
            session.add(
                model.consumption(
                    id=f"G-{period_from:%Y%m%d%H%M}",
                    energy="G",
                    period_from=period_from,
                    period_to=period_from + timedelta(minutes=30),
                    raw_value=kwh / 2,
                    unit="kWh",
                    est_kwh=kwh / 2,
                )
            )


def _record_quiet_evening_hours(client: MariaDBClient, days: int, kwh: float) -> None:
    """`days` earlier days, each with a heating-off 18:00 hour of `kwh` of gas
    (hot water and cooking), the hourly job run at the end of each day."""
    for back in range(days, 0, -1):
        hour = HOUR - timedelta(days=back)
        _seed_polls(client, hour, on=0)
        _seed_gas(client, hour, kwh)
        HeatingHourJob(client, REGION).refresh(hour.replace(hour=23, tzinfo=UTC))


def test_a_metered_heating_hour_is_the_gas_less_the_hot_water_baseline(
    mariadb_client: MariaDBClient,
) -> None:
    # Given the 18:00 hour has used 0.3 kWh on six earlier days with no heating
    _seed_gas_rate(mariadb_client)
    _record_quiet_evening_hours(mariadb_client, days=6, kwh=0.3)
    # and today's 18:00 hour had the heating on for 24 minutes and 4.5 kWh of gas
    _seed_polls(mariadb_client, HOUR, on=12)
    _seed_gas(mariadb_client, HOUR, 4.5)

    # When the hourly job runs
    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then the hour is measured, and its heating is the gas less the 0.3 kWh
    # of hot water and cooking typical of that hour, priced at the rate in force
    row = _heating_hours(mariadb_client)[-1]
    assert row.hour_start == HOUR
    assert row.source == "measured"
    assert row.measured_kwh == pytest.approx(4.5)
    assert row.heating_kwh == pytest.approx(4.2)
    assert row.cost_gbp == pytest.approx(4.2 * GAS_UNIT_RATE_P / 100)


def _record_quiet_hours(
    client: MariaDBClient, at: int, days: range, kwh: float
) -> None:
    """A heating-off hour at `at`:00 of `kwh` of gas on each of the days back."""
    for back in days:
        hour = HOUR.replace(hour=at) - timedelta(days=back)
        _seed_polls(client, hour, on=0)
        _seed_gas(client, hour, kwh)
        HeatingHourJob(client, REGION).refresh(hour.replace(hour=23, tzinfo=UTC))


def _clear_gas(client: MariaDBClient) -> None:
    with client.session_write_scope() as session:
        session.query(model.consumption).delete()


def test_a_heating_off_hour_with_gas_is_hot_water_and_costs_nothing(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a heating-off hour that used 0.3 kWh of gas
    _seed_gas_rate(mariadb_client)
    _seed_polls(mariadb_client, HOUR, on=0)
    _seed_gas(mariadb_client, HOUR, 0.3)

    # When the hourly job runs
    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then it is measured with no heating, its gas kept as measured use
    row = _heating_hours(mariadb_client)[-1]
    assert row.source == "measured"
    assert row.heating_kwh == 0
    assert row.cost_gbp == 0
    assert row.measured_kwh == pytest.approx(0.3)


def test_a_thinly_evidenced_hour_of_day_uses_the_median_of_all_hours_of_day(
    mariadb_client: MariaDBClient,
) -> None:
    # Given six 10:00 heating-off hours at 0.2 kWh, but only two at 18:00 (0.6)
    _seed_gas_rate(mariadb_client)
    _record_quiet_hours(mariadb_client, 10, range(6, 0, -1), 0.2)
    _record_quiet_hours(mariadb_client, 18, range(2, 0, -1), 0.6)
    _seed_polls(mariadb_client, HOUR, on=12)
    _seed_gas(mariadb_client, HOUR, 4.5)

    # When the hourly job runs
    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then 18:00 uses the median over all hours (0.2), not its own two
    assert _heating_hours(mariadb_client)[-1].heating_kwh == pytest.approx(4.3)


def test_without_any_heating_off_hours_the_baseline_is_zero(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a heating hour with gas and no heating-off hours to learn from
    _seed_gas_rate(mariadb_client)
    _seed_polls(mariadb_client, HOUR, on=12)
    _seed_gas(mariadb_client, HOUR, 4.5)

    # When the hourly job runs
    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then all of the gas is heating
    assert _heating_hours(mariadb_client)[-1].heating_kwh == pytest.approx(4.5)


def test_late_arriving_gas_turns_an_estimated_hour_into_a_measured_one(
    mariadb_client: MariaDBClient,
) -> None:
    # Given an hour first written as estimated
    _seed_gas_rate(mariadb_client)
    _seed_polls(mariadb_client, HOUR, on=12)
    HeatingHourJob(mariadb_client, REGION).refresh(NOW)
    assert _heating_hours(mariadb_client)[-1].source == "estimated"

    # When its gas arrives and the job runs again
    _seed_gas(mariadb_client, HOUR, 4.5)
    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then the row is measured
    row = _heating_hours(mariadb_client)[-1]
    assert row.source == "measured"
    assert row.measured_kwh == pytest.approx(4.5)


def test_a_day_of_zero_readings_is_not_taken_as_measured_use(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a London day whose only readings are zero, for a heating-off hour
    _seed_gas_rate(mariadb_client)
    _seed_polls(mariadb_client, HOUR, on=0)
    _seed_gas(mariadb_client, HOUR, 0.0)

    # When the hourly job runs
    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then the hour stays estimated, with nothing measured
    row = _heating_hours(mariadb_client)[-1]
    assert row.source == "estimated"
    assert row.measured_kwh is None
    assert row.heating_kwh == 0


def test_a_measured_hour_outside_the_recompute_window_is_never_rewritten(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a measured hour from 60 days ago, whose raw gas has since been pruned
    _seed_gas_rate(mariadb_client)
    old = HOUR - timedelta(days=60)
    _seed_polls(mariadb_client, old, on=12)
    _seed_gas(mariadb_client, old, 4.5)
    HeatingHourJob(mariadb_client, REGION).refresh(old.replace(hour=20, tzinfo=UTC))
    _clear_gas(mariadb_client)

    # When the hourly job runs today
    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then the old row is still measured
    row = _heating_hours(mariadb_client)[0]
    assert (row.hour_start, row.source) == (old, "measured")
    assert row.measured_kwh == pytest.approx(4.5)


def test_measured_heating_never_goes_below_zero(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a 0.3 kWh baseline and a heating hour that metered only 0.1 kWh
    _seed_gas_rate(mariadb_client)
    _record_quiet_evening_hours(mariadb_client, days=6, kwh=0.3)
    _seed_polls(mariadb_client, HOUR, on=12)
    _seed_gas(mariadb_client, HOUR, 0.1)

    # When the hourly job runs
    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then its heating is 0, not negative
    assert _heating_hours(mariadb_client)[-1].heating_kwh == 0


def _record_heating_evening_hours(
    client: MariaDBClient, days: int, on_polls: int, kwh: float, ago: int = 1
) -> None:
    """`days` earlier days, from `ago` days back, each with an 18:00 hour of
    `on_polls` ON polls and `kwh` of gas, the hourly job run at the end of each
    day."""
    for back in range(ago + days - 1, ago - 1, -1):
        hour = HOUR - timedelta(days=back)
        _seed_polls(client, hour, on=on_polls)
        _seed_gas(client, hour, kwh)
        HeatingHourJob(client, REGION).refresh(hour.replace(hour=23, tzinfo=UTC))


def test_an_estimated_hour_uses_the_boiler_kw_learned_from_metered_hours(
    mariadb_client: MariaDBClient,
) -> None:
    # Given five earlier hours with the heating on for 40 minutes that each used
    # 6 kWh of gas: 30 kWh over 3.33 hours on, so the boiler drew 9 kW
    _seed_gas_rate(mariadb_client)
    _record_heating_evening_hours(mariadb_client, days=5, on_polls=20, kwh=6.0)
    # and an hour with the heating on for 24 minutes whose gas has not arrived
    _seed_polls(mariadb_client, HOUR, on=12)

    # When the hourly job runs
    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then the hour is estimated at the learned 9 kW, not the 12 kW default
    row = _heating_hours(mariadb_client)[-1]
    assert row.source == "estimated"
    assert row.heating_kwh == pytest.approx(24 / 60 * 9)
    assert row.cost_gbp == pytest.approx(24 / 60 * 9 * GAS_UNIT_RATE_P / 100)


def test_an_estimated_hour_uses_the_default_kw_with_under_five_metered_hours(
    mariadb_client: MariaDBClient,
) -> None:
    # Given only four earlier metered hours with the heating on for 40 minutes
    _seed_gas_rate(mariadb_client)
    _record_heating_evening_hours(mariadb_client, days=4, on_polls=20, kwh=6.0)
    _seed_polls(mariadb_client, HOUR, on=12)

    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then the hour is estimated at the 12 kW default
    assert _heating_hours(mariadb_client)[-1].heating_kwh == pytest.approx(24 / 60 * 12)


def test_metered_hours_older_than_28_days_do_not_affect_the_learned_kw(
    mariadb_client: MariaDBClient,
) -> None:
    # Given five metered hours drawing 9 kW, all from 40 or more days ago
    _seed_gas_rate(mariadb_client)
    _record_heating_evening_hours(mariadb_client, days=5, on_polls=20, kwh=6.0, ago=40)
    _seed_polls(mariadb_client, HOUR, on=12)

    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then the estimate still uses the 12 kW default
    assert _heating_hours(mariadb_client)[-1].heating_kwh == pytest.approx(24 / 60 * 12)


def test_each_hour_is_priced_at_the_rate_in_force_at_its_start(
    mariadb_client: MariaDBClient,
) -> None:
    # Given the gas rate rises from 6p to 8p at 15:00 on the day
    _seed_gas_rate(mariadb_client)
    with mariadb_client.session_write_scope() as session:
        session.add(
            model.product_rate(
                id="G-rate-2",
                product_code=GAS_PRODUCT,
                region=REGION,
                valid_from=datetime(2026, 11, 2, 15, 0),
                valid_to=None,
                unit_rate=8.0,
                standing_charge=29.0,
            )
        )
    # and the heating was on for 24 minutes at 12:00 and at 18:00
    _seed_polls(mariadb_client, HOUR.replace(hour=12), on=12)
    _seed_polls(mariadb_client, HOUR, on=12)

    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then each hour is priced at the rate in force when it started
    before, after = _heating_hours(mariadb_client)
    assert before.unit_rate == pytest.approx(6.0)
    assert before.cost_gbp == pytest.approx(24 / 60 * 12 * 6.0 / 100)
    assert after.unit_rate == pytest.approx(8.0)
    assert after.cost_gbp == pytest.approx(24 / 60 * 12 * 8.0 / 100)


def test_on_a_25_hour_day_each_utc_hour_has_one_row_and_baselines_follow_london_hour(
    mariadb_client: MariaDBClient,
) -> None:
    # Given London 01:00 hours (00:00 UTC in BST) used 0.3 kWh and London 02:00
    # hours (01:00 UTC in BST) used 1.0 kWh on five earlier days with no heating
    _seed_gas_rate(mariadb_client)
    for back in range(10, 5, -1):
        day = datetime(2026, 10, 25) - timedelta(days=back)
        for hour, kwh in ((day, 0.3), (day + timedelta(hours=1), 1.0)):
            _seed_polls(mariadb_client, hour, on=0)
            _seed_gas(mariadb_client, hour, kwh)
            HeatingHourJob(mariadb_client, REGION).refresh(
                hour.replace(hour=23, tzinfo=UTC)
            )
    # and on 25 October, when London falls back, both UTC hours that are London
    # 01:00 (BST, then GMT) had the heating on for 24 minutes and 4.5 kWh of gas
    for hour in (datetime(2026, 10, 25, 0), datetime(2026, 10, 25, 1)):
        _seed_polls(mariadb_client, hour, on=12)
        _seed_gas(mariadb_client, hour, 4.5)

    # When the hourly job runs the next morning
    HeatingHourJob(mariadb_client, REGION).refresh(
        datetime(2026, 10, 26, 6, 0, tzinfo=UTC)
    )

    # Then each UTC hour has one row, both less the London 01:00 baseline of 0.3
    rows = [r for r in _heating_hours(mariadb_client) if r.hour_start.day == 25]
    assert [r.hour_start.hour for r in rows] == [0, 1]
    assert [r.heating_kwh for r in rows] == [pytest.approx(4.2)] * 2


def test_the_first_run_on_an_empty_table_fills_every_hour_back_to_the_first_poll(
    mariadb_client: MariaDBClient,
) -> None:
    # Given an empty heating_hour table and a heating hour on each of ten days
    _seed_gas_rate(mariadb_client)
    for back in range(10, 0, -1):
        _seed_polls(mariadb_client, HOUR - timedelta(days=back), on=12)

    # When the hourly job runs for the first time
    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then every one of those hours has a row, not only those of the last few days
    hours = [row.hour_start for row in _heating_hours(mariadb_client)]
    assert hours == [HOUR - timedelta(days=back) for back in range(10, 0, -1)]


def test_each_backfilled_day_learns_only_from_the_days_before_it(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a heating hour on each of ten days; the five middle days are metered
    # at 6 kW (2.4 kWh in 24 minutes), the first and the last four have no gas
    _seed_gas_rate(mariadb_client)
    for back in range(10, 0, -1):
        hour = HOUR - timedelta(days=back)
        _seed_polls(mariadb_client, hour, on=12)
        if 5 <= back <= 9:
            _seed_gas(mariadb_client, hour, 2.4)

    # When the hourly job runs for the first time
    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then the first day, with nothing measured before it, is estimated at the
    # 12 kW default, and a later day at the 6 kW learned from the days before it
    rows = {row.hour_start: row for row in _heating_hours(mariadb_client)}
    assert rows[HOUR - timedelta(days=10)].heating_kwh == pytest.approx(0.4 * 12)
    assert rows[HOUR - timedelta(days=4)].heating_kwh == pytest.approx(0.4 * 6)


def test_a_backfilled_hour_older_than_the_retained_gas_is_estimated(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a heating hour from 60 days ago and no gas data any more
    _seed_gas_rate(mariadb_client)
    old = HOUR - timedelta(days=60)
    _seed_polls(mariadb_client, old, on=12)

    # When the hourly job runs for the first time
    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then the old hour is an estimate
    [row] = _heating_hours(mariadb_client)
    assert (row.hour_start, row.source) == (old, "estimated")


def test_a_run_on_a_populated_table_recomputes_only_the_last_four_london_days(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a populated table with an old row that has been altered
    _seed_gas_rate(mariadb_client)
    old = HOUR - timedelta(days=10)
    _seed_polls(mariadb_client, old, on=12)
    _seed_polls(mariadb_client, HOUR, on=12)
    HeatingHourJob(mariadb_client, REGION).refresh(NOW)
    stamp = datetime(2000, 1, 1)
    with mariadb_client.session_write_scope() as session:
        session.query(model.heating_hour).filter(
            model.heating_hour.hour_start == old
        ).update({"heating_kwh": 99.0, "computed_at": stamp})

    # When the hourly job runs again
    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then the old row is untouched
    row = _heating_hours(mariadb_client)[0]
    assert (row.hour_start, row.heating_kwh, row.computed_at) == (old, 99.0, stamp)


def test_a_run_after_a_long_stop_fills_the_days_the_job_missed(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a job that stopped ten days ago, leaving only its early rows
    _seed_gas_rate(mariadb_client)
    for back in range(10, 0, -1):
        _seed_polls(mariadb_client, HOUR - timedelta(days=back), on=12)
    HeatingHourJob(mariadb_client, REGION).refresh(NOW - timedelta(days=9))
    assert len(_heating_hours(mariadb_client)) == 2

    # When the job runs again
    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then every hour since the stop has its row
    hours = [row.hour_start for row in _heating_hours(mariadb_client)]
    assert hours == [HOUR - timedelta(days=back) for back in range(10, 0, -1)]


def test_an_hour_after_the_last_gas_rate_ended_has_no_rate_or_cost(
    mariadb_client: MariaDBClient,
) -> None:
    # Given the only gas rate ended on 1 November with no rate after it
    _seed_gas_rate(mariadb_client)
    with mariadb_client.session_write_scope() as session:
        session.query(model.product_rate).update({"valid_to": datetime(2026, 11, 1)})
    _seed_polls(mariadb_client, HOUR, on=12)

    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then the 2 November hour is not priced at the expired rate
    [row] = _heating_hours(mariadb_client)
    assert row.unit_rate is None
    assert row.cost_gbp is None


def test_a_metered_hour_with_no_gas_rate_in_force_keeps_its_gas_and_has_no_cost(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_polls(mariadb_client, HOUR, on=12)
    _seed_gas(mariadb_client, HOUR, 4.5)

    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    [row] = _heating_hours(mariadb_client)
    assert row.source == "measured"
    assert row.heating_kwh == pytest.approx(4.5)
    assert row.cost_gbp is None


def test_a_learned_boiler_kw_of_zero_falls_back_to_the_default(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a heating-off 12:00 hour that used 0.5 kWh on five earlier days, and
    # 18:00 heating hours on the same days that metered only 0.4 kWh, less than
    # that hot-water baseline, so their heating works out at zero
    _seed_gas_rate(mariadb_client)
    for back in range(5, 0, -1):
        quiet = HOUR.replace(hour=12) - timedelta(days=back)
        _seed_polls(mariadb_client, quiet, on=0)
        _seed_gas(mariadb_client, quiet, 0.5)
        busy = HOUR - timedelta(days=back)
        _seed_polls(mariadb_client, busy, on=20)
        _seed_gas(mariadb_client, busy, 0.4)
        HeatingHourJob(mariadb_client, REGION).refresh(
            busy.replace(hour=23, tzinfo=UTC)
        )
    _seed_polls(mariadb_client, HOUR, on=12)

    HeatingHourJob(mariadb_client, REGION).refresh(NOW)

    # Then the unmetered hour is estimated at the 12 kW default, not at zero
    row = _heating_hours(mariadb_client)[-1]
    assert row.source == "estimated"
    assert row.heating_kwh == pytest.approx(24 / 60 * 12)
