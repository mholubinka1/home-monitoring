from datetime import UTC, datetime, timedelta

import pytest

from octopus_app.data.heating_interval_job import HeatingIntervalJob
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient, heating_status_table

REGION = "C"
GAS_PRODUCT = "VAR-22-11-01"
GAS_UNIT_RATE_P = 6.0
HALF_HOUR = timedelta(minutes=30)
START = datetime(2026, 11, 2, 18, 0)  # 18:00 UTC; London is on GMT, so also 18:00
NOW = datetime(2026, 11, 2, 20, 0, tzinfo=UTC)
FULL_INTERVAL_POLLS = 15  # a 2-minute poll interval
ON_POLLS = 6  # 12 minutes on
ON_POLLS_KWH_AT_12KW = 12 / 60 * 12


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
    start: datetime,
    *,
    on: int,
    total: int = FULL_INTERVAL_POLLS,
    boost: bool = False,
) -> None:
    """`total` two-minute polls from `start`, the first `on` of them with the
    thermostat calling for heat."""
    rows = [
        {
            "polled_at": start + timedelta(minutes=2 * i),
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


def _seed_gas(client: MariaDBClient, start: datetime, kwh: float) -> None:
    """The half-hourly gas reading of the half-hour from `start`."""
    with client.session_write_scope() as session:
        session.add(
            model.consumption(
                id=f"G-{start:%Y%m%d%H%M}",
                energy="G",
                period_from=start,
                period_to=start + HALF_HOUR,
                raw_value=kwh,
                unit="kWh",
                est_kwh=kwh,
            )
        )


def _clear_gas(client: MariaDBClient) -> None:
    with client.session_write_scope() as session:
        session.query(model.consumption).delete()


def _job(client: MariaDBClient) -> HeatingIntervalJob:
    return HeatingIntervalJob(client, REGION)


def _heating_intervals(client: MariaDBClient) -> list[model.heating_interval]:
    with client.session_read_scope() as session:
        rows = session.query(model.heating_interval).order_by(
            model.heating_interval.starts_at
        )
        return list(rows)


def _record_quiet_intervals(
    client: MariaDBClient, start: datetime, days: range, kwh: float
) -> None:
    """A heating-off half-hour of `kwh` of gas at `start`'s time on each of the
    days back, the job run at the end of each day."""
    for back in days:
        interval = start - timedelta(days=back)
        _seed_polls(client, interval, on=0)
        _seed_gas(client, interval, kwh)
        _job(client).refresh(interval.replace(hour=23, minute=59, tzinfo=UTC))


def _record_heating_intervals(
    client: MariaDBClient, days: int, on_polls: int, kwh: float, ago: int = 1
) -> None:
    """`days` earlier days, from `ago` days back, each with an 18:00 half-hour of
    `on_polls` ON polls and `kwh` of gas, the job run at the end of each day."""
    for back in range(ago + days - 1, ago - 1, -1):
        interval = START - timedelta(days=back)
        _seed_polls(client, interval, on=on_polls)
        _seed_gas(client, interval, kwh)
        _job(client).refresh(interval.replace(hour=23, minute=59, tzinfo=UTC))


def test_a_half_hour_the_heating_was_on_is_estimated_and_priced_when_there_is_no_gas_yet(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 6 ON polls (12 minutes) in a completed half-hour, and no gas data
    _seed_gas_rate(mariadb_client)
    _seed_polls(mariadb_client, START, on=ON_POLLS)

    # When the job runs
    _job(mariadb_client).refresh(NOW)

    # Then one row carries its start, end and minutes on, estimated at the 12 kW
    # default and priced at the gas unit rate in force
    [row] = _heating_intervals(mariadb_client)
    assert row.starts_at == START
    assert row.ends_at == START + HALF_HOUR
    assert row.poll_count == FULL_INTERVAL_POLLS
    assert row.on_minutes == 12
    assert row.source == "estimated"
    assert row.heating_kwh == pytest.approx(ON_POLLS_KWH_AT_12KW)
    assert row.unit_rate == pytest.approx(GAS_UNIT_RATE_P)
    assert row.cost_gbp == pytest.approx(ON_POLLS_KWH_AT_12KW * GAS_UNIT_RATE_P / 100)


def test_polls_with_boost_active_count_as_heating_on(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_polls(mariadb_client, START, on=ON_POLLS, boost=True)

    _job(mariadb_client).refresh(NOW)

    [row] = _heating_intervals(mariadb_client)
    assert row.on_minutes == 12


def test_a_half_hour_with_too_few_polls_gets_no_row_and_loses_an_existing_one(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_polls(mariadb_client, START, on=ON_POLLS)
    job = _job(mariadb_client)
    job.refresh(NOW)
    assert len(_heating_intervals(mariadb_client)) == 1

    with mariadb_client.session_write_scope() as session:
        session.execute(heating_status_table.delete())
    _seed_polls(mariadb_client, START, on=ON_POLLS, total=11)
    job.refresh(NOW)

    assert not _heating_intervals(mariadb_client)


def test_minutes_on_never_exceed_the_half_hour_when_a_retried_poll_adds_one(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 16 polls in the half-hour, 110 seconds apart, all with the heating on
    rows = [
        {
            "polled_at": START + timedelta(seconds=110 * index),
            "current_temp": 19.0,
            "target_temp": 21.0,
            "mode": "SCHEDULE",
            "state": "ON",
            "boost_active": False,
        }
        for index in range(16)
    ]
    with mariadb_client.session_write_scope() as session:
        session.execute(heating_status_table.insert(), rows)

    _job(mariadb_client).refresh(NOW)

    # Then 30 minutes are on, not 32
    [row] = _heating_intervals(mariadb_client)
    assert row.poll_count == 16
    assert row.on_minutes == 30


def test_a_half_hour_with_twelve_polls_gets_a_row(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_polls(mariadb_client, START, on=ON_POLLS, total=12)

    _job(mariadb_client).refresh(NOW)

    [row] = _heating_intervals(mariadb_client)
    assert row.poll_count == 12


def test_the_current_unfinished_half_hour_gets_no_row(
    mariadb_client: MariaDBClient,
) -> None:
    current = NOW.replace(tzinfo=None)
    _seed_polls(mariadb_client, current, on=ON_POLLS)

    _job(mariadb_client).refresh(NOW + timedelta(minutes=29))

    assert not _heating_intervals(mariadb_client)


def test_a_half_hour_with_no_gas_rate_in_force_is_kept_without_a_rate_or_cost(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_polls(mariadb_client, START, on=ON_POLLS)

    _job(mariadb_client).refresh(NOW)

    [row] = _heating_intervals(mariadb_client)
    assert row.heating_kwh == pytest.approx(ON_POLLS_KWH_AT_12KW)
    assert row.unit_rate is None
    assert row.cost_gbp is None


def test_nothing_is_written_when_hive_apps_table_does_not_exist(
    mariadb_client_without_weather_tables: MariaDBClient,
) -> None:
    client = mariadb_client_without_weather_tables

    _job(client).refresh(NOW)

    assert not _heating_intervals(client)


def test_a_metered_half_hour_is_the_gas_less_the_hot_water_baseline(
    mariadb_client: MariaDBClient,
) -> None:
    # Given the 18:00 half-hour has used 0.15 kWh on six earlier days with no heating
    _seed_gas_rate(mariadb_client)
    _record_quiet_intervals(mariadb_client, START, range(6, 0, -1), 0.15)
    # and today's 18:00 half-hour had the heating on for 12 minutes and 2.25 kWh
    _seed_polls(mariadb_client, START, on=ON_POLLS)
    _seed_gas(mariadb_client, START, 2.25)

    # When the job runs
    _job(mariadb_client).refresh(NOW)

    # Then the half-hour is measured, and its heating is the gas less the 0.15 kWh
    # of hot water and cooking typical of that half-hour, priced at the rate in force
    row = _heating_intervals(mariadb_client)[-1]
    assert row.starts_at == START
    assert row.source == "measured"
    assert row.measured_kwh == pytest.approx(2.25)
    assert row.heating_kwh == pytest.approx(2.1)
    assert row.cost_gbp == pytest.approx(2.1 * GAS_UNIT_RATE_P / 100)


def test_a_heating_off_half_hour_with_gas_is_hot_water_and_costs_nothing(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_gas_rate(mariadb_client)
    _seed_polls(mariadb_client, START, on=0)
    _seed_gas(mariadb_client, START, 0.15)

    _job(mariadb_client).refresh(NOW)

    row = _heating_intervals(mariadb_client)[-1]
    assert row.source == "measured"
    assert row.heating_kwh == 0
    assert row.cost_gbp == 0
    assert row.measured_kwh == pytest.approx(0.15)


def test_a_thinly_evidenced_half_hour_of_day_uses_the_median_of_all_of_them(
    mariadb_client: MariaDBClient,
) -> None:
    # Given six 10:00 heating-off half-hours at 0.1 kWh, but only two at 18:00 (0.3)
    _seed_gas_rate(mariadb_client)
    _record_quiet_intervals(
        mariadb_client, START.replace(hour=10), range(6, 0, -1), 0.1
    )
    _record_quiet_intervals(mariadb_client, START, range(2, 0, -1), 0.3)
    _seed_polls(mariadb_client, START, on=ON_POLLS)
    _seed_gas(mariadb_client, START, 2.25)

    _job(mariadb_client).refresh(NOW)

    # Then 18:00 uses the median over all of them (0.1), not its own two
    assert _heating_intervals(mariadb_client)[-1].heating_kwh == pytest.approx(2.15)


def test_a_half_hour_of_day_with_five_samples_uses_its_own_median(
    mariadb_client: MariaDBClient,
) -> None:
    # Given five 18:00 heating-off half-hours at 0.3 kWh and six 10:00 ones at 0.1
    _seed_gas_rate(mariadb_client)
    _record_quiet_intervals(
        mariadb_client, START.replace(hour=10), range(6, 0, -1), 0.1
    )
    _record_quiet_intervals(mariadb_client, START, range(5, 0, -1), 0.3)
    _seed_polls(mariadb_client, START, on=ON_POLLS)
    _seed_gas(mariadb_client, START, 2.25)

    _job(mariadb_client).refresh(NOW)

    assert _heating_intervals(mariadb_client)[-1].heating_kwh == pytest.approx(1.95)


def test_without_any_heating_off_half_hours_the_baseline_is_zero(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_gas_rate(mariadb_client)
    _seed_polls(mariadb_client, START, on=ON_POLLS)
    _seed_gas(mariadb_client, START, 2.25)

    _job(mariadb_client).refresh(NOW)

    assert _heating_intervals(mariadb_client)[-1].heating_kwh == pytest.approx(2.25)


def test_late_arriving_gas_turns_an_estimated_half_hour_into_a_measured_one(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a half-hour first written as estimated
    _seed_gas_rate(mariadb_client)
    _seed_polls(mariadb_client, START, on=ON_POLLS)
    _job(mariadb_client).refresh(NOW)
    assert _heating_intervals(mariadb_client)[-1].source == "estimated"

    # When its gas arrives and the job runs again
    _seed_gas(mariadb_client, START, 2.25)
    _job(mariadb_client).refresh(NOW)

    # Then the row is measured
    row = _heating_intervals(mariadb_client)[-1]
    assert row.source == "measured"
    assert row.measured_kwh == pytest.approx(2.25)


def test_a_day_of_zero_readings_is_not_taken_as_measured_use(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a London day whose only readings are zero, for a heating-off half-hour
    _seed_gas_rate(mariadb_client)
    _seed_polls(mariadb_client, START, on=0)
    _seed_gas(mariadb_client, START, 0.0)

    _job(mariadb_client).refresh(NOW)

    # Then the half-hour stays estimated, with nothing measured
    row = _heating_intervals(mariadb_client)[-1]
    assert row.source == "estimated"
    assert row.measured_kwh is None
    assert row.heating_kwh == 0


def test_a_measured_half_hour_outside_the_recompute_window_is_never_rewritten(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a measured half-hour from 60 days ago, whose raw gas has since been pruned
    _seed_gas_rate(mariadb_client)
    old = START - timedelta(days=60)
    _seed_polls(mariadb_client, old, on=ON_POLLS)
    _seed_gas(mariadb_client, old, 2.25)
    _job(mariadb_client).refresh(old.replace(hour=20, tzinfo=UTC))
    _clear_gas(mariadb_client)

    # When the job runs today
    _job(mariadb_client).refresh(NOW)

    # Then the old row is still measured
    row = _heating_intervals(mariadb_client)[0]
    assert (row.starts_at, row.source) == (old, "measured")
    assert row.measured_kwh == pytest.approx(2.25)


def test_measured_heating_never_goes_below_zero(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a 0.15 kWh baseline and a heating half-hour that metered only 0.05 kWh
    _seed_gas_rate(mariadb_client)
    _record_quiet_intervals(mariadb_client, START, range(6, 0, -1), 0.15)
    _seed_polls(mariadb_client, START, on=ON_POLLS)
    _seed_gas(mariadb_client, START, 0.05)

    _job(mariadb_client).refresh(NOW)

    assert _heating_intervals(mariadb_client)[-1].heating_kwh == 0


def test_an_estimated_half_hour_uses_the_boiler_kw_learned_from_metered_half_hours(
    mariadb_client: MariaDBClient,
) -> None:
    # Given ten earlier half-hours with the heating on for 20 minutes that each used
    # 3 kWh of gas: 30 kWh over 3.33 hours on, so the boiler drew 9 kW
    _seed_gas_rate(mariadb_client)
    _record_heating_intervals(mariadb_client, days=10, on_polls=10, kwh=3.0)
    # and a half-hour with the heating on for 12 minutes whose gas has not arrived
    _seed_polls(mariadb_client, START, on=ON_POLLS)

    _job(mariadb_client).refresh(NOW)

    # Then the half-hour is estimated at the learned 9 kW, not the 12 kW default
    row = _heating_intervals(mariadb_client)[-1]
    assert row.source == "estimated"
    assert row.heating_kwh == pytest.approx(12 / 60 * 9)
    assert row.cost_gbp == pytest.approx(12 / 60 * 9 * GAS_UNIT_RATE_P / 100)


def test_an_estimated_half_hour_uses_the_default_kw_with_under_ten_metered_ones(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_gas_rate(mariadb_client)
    _record_heating_intervals(mariadb_client, days=9, on_polls=10, kwh=3.0)
    _seed_polls(mariadb_client, START, on=ON_POLLS)

    _job(mariadb_client).refresh(NOW)

    assert _heating_intervals(mariadb_client)[-1].heating_kwh == pytest.approx(
        ON_POLLS_KWH_AT_12KW
    )


def test_half_hours_with_under_ten_minutes_on_do_not_teach_the_boiler_kw(
    mariadb_client: MariaDBClient,
) -> None:
    # Given ten metered half-hours with the heating on for only 8 minutes
    _seed_gas_rate(mariadb_client)
    _record_heating_intervals(mariadb_client, days=10, on_polls=4, kwh=3.0)
    _seed_polls(mariadb_client, START, on=ON_POLLS)

    _job(mariadb_client).refresh(NOW)

    assert _heating_intervals(mariadb_client)[-1].heating_kwh == pytest.approx(
        ON_POLLS_KWH_AT_12KW
    )


def test_metered_half_hours_older_than_28_days_do_not_affect_the_learned_kw(
    mariadb_client: MariaDBClient,
) -> None:
    # Given ten metered half-hours drawing 9 kW, all from 40 or more days ago
    _seed_gas_rate(mariadb_client)
    _record_heating_intervals(mariadb_client, days=10, on_polls=10, kwh=3.0, ago=40)
    _seed_polls(mariadb_client, START, on=ON_POLLS)

    _job(mariadb_client).refresh(NOW)

    assert _heating_intervals(mariadb_client)[-1].heating_kwh == pytest.approx(
        ON_POLLS_KWH_AT_12KW
    )


def test_each_half_hour_is_priced_at_the_rate_in_force_at_its_start(
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
    # and the heating was on for 12 minutes at 12:00 and at 18:00
    _seed_polls(mariadb_client, START.replace(hour=12), on=ON_POLLS)
    _seed_polls(mariadb_client, START, on=ON_POLLS)

    _job(mariadb_client).refresh(NOW)

    before, after = _heating_intervals(mariadb_client)
    assert before.unit_rate == pytest.approx(6.0)
    assert before.cost_gbp == pytest.approx(ON_POLLS_KWH_AT_12KW * 6.0 / 100)
    assert after.unit_rate == pytest.approx(8.0)
    assert after.cost_gbp == pytest.approx(ON_POLLS_KWH_AT_12KW * 8.0 / 100)


def test_on_a_25_hour_day_each_utc_half_hour_has_one_row_and_baselines_follow_london_time(
    mariadb_client: MariaDBClient,
) -> None:
    # Given London 01:00 half-hours (00:00 UTC in BST) used 0.15 kWh and London 02:00
    # half-hours (01:00 UTC in BST) used 0.5 kWh on five earlier days, no heating
    _seed_gas_rate(mariadb_client)
    for back in range(10, 5, -1):
        day = datetime(2026, 10, 25) - timedelta(days=back)
        for interval, kwh in ((day, 0.15), (day + timedelta(hours=1), 0.5)):
            _seed_polls(mariadb_client, interval, on=0)
            _seed_gas(mariadb_client, interval, kwh)
            _job(mariadb_client).refresh(interval.replace(hour=23, tzinfo=UTC))
    # and on 25 October, when London falls back, both UTC half-hours that are London
    # 01:00 (BST, then GMT) had the heating on for 12 minutes and 2.25 kWh of gas
    for interval in (datetime(2026, 10, 25, 0), datetime(2026, 10, 25, 1)):
        _seed_polls(mariadb_client, interval, on=ON_POLLS)
        _seed_gas(mariadb_client, interval, 2.25)

    # When the job runs the next morning
    _job(mariadb_client).refresh(datetime(2026, 10, 26, 6, 0, tzinfo=UTC))

    # Then each UTC half-hour has one row, both less the London 01:00 baseline of 0.15
    rows = [r for r in _heating_intervals(mariadb_client) if r.starts_at.day == 25]
    assert [r.starts_at.hour for r in rows] == [0, 1]
    assert [r.heating_kwh for r in rows] == [pytest.approx(2.1)] * 2


def test_the_first_run_on_an_empty_table_fills_every_half_hour_back_to_the_first_poll(
    mariadb_client: MariaDBClient,
) -> None:
    # Given an empty heating_interval table and a heating half-hour on each of ten days
    _seed_gas_rate(mariadb_client)
    for back in range(10, 0, -1):
        _seed_polls(mariadb_client, START - timedelta(days=back), on=ON_POLLS)

    # When the job runs for the first time
    _job(mariadb_client).refresh(NOW)

    # Then every one of them has a row, not only those of the last few days
    starts = [row.starts_at for row in _heating_intervals(mariadb_client)]
    assert starts == [START - timedelta(days=back) for back in range(10, 0, -1)]


def test_each_backfilled_day_learns_from_its_own_and_earlier_days_never_later_ones(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a heating half-hour on each of twenty days; the ten middle days are
    # metered at 6 kW (1.2 kWh in 12 minutes), the first five and the last five
    # have no gas
    _seed_gas_rate(mariadb_client)
    for back in range(20, 0, -1):
        interval = START - timedelta(days=back)
        _seed_polls(mariadb_client, interval, on=ON_POLLS)
        if 6 <= back <= 15:
            _seed_gas(mariadb_client, interval, 1.2)

    # When the job runs for the first time
    _job(mariadb_client).refresh(NOW)

    # Then the first day, with nothing measured before it, is estimated at the
    # 12 kW default, and a later day at the 6 kW learned from the days before it
    rows = {row.starts_at: row for row in _heating_intervals(mariadb_client)}
    assert rows[START - timedelta(days=20)].heating_kwh == pytest.approx(0.2 * 12)
    assert rows[START - timedelta(days=4)].heating_kwh == pytest.approx(0.2 * 6)


def test_a_backfilled_half_hour_older_than_the_retained_gas_is_estimated(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_gas_rate(mariadb_client)
    old = START - timedelta(days=60)
    _seed_polls(mariadb_client, old, on=ON_POLLS)

    _job(mariadb_client).refresh(NOW)

    [row] = _heating_intervals(mariadb_client)
    assert (row.starts_at, row.source) == (old, "estimated")


def test_a_run_on_a_populated_table_recomputes_only_the_last_four_london_days(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a populated table with an old row that has been altered
    _seed_gas_rate(mariadb_client)
    old = START - timedelta(days=10)
    _seed_polls(mariadb_client, old, on=ON_POLLS)
    _seed_polls(mariadb_client, START, on=ON_POLLS)
    _job(mariadb_client).refresh(NOW)
    stamp = datetime(2000, 1, 1)
    with mariadb_client.session_write_scope() as session:
        session.query(model.heating_interval).filter(
            model.heating_interval.starts_at == old
        ).update({"heating_kwh": 99.0, "computed_at": stamp})

    # When the job runs again
    _job(mariadb_client).refresh(NOW)

    # Then the old row is untouched
    row = _heating_intervals(mariadb_client)[0]
    assert (row.starts_at, row.heating_kwh, row.computed_at) == (old, 99.0, stamp)


def test_a_run_after_a_long_stop_fills_the_days_the_job_missed(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a job that stopped ten days ago, leaving only its early rows
    _seed_gas_rate(mariadb_client)
    for back in range(10, 0, -1):
        _seed_polls(mariadb_client, START - timedelta(days=back), on=ON_POLLS)
    _job(mariadb_client).refresh(NOW - timedelta(days=9))
    assert len(_heating_intervals(mariadb_client)) == 2

    # When the job runs again
    _job(mariadb_client).refresh(NOW)

    # Then every half-hour since the stop has its row
    starts = [row.starts_at for row in _heating_intervals(mariadb_client)]
    assert starts == [START - timedelta(days=back) for back in range(10, 0, -1)]


def test_a_half_hour_after_the_last_gas_rate_ended_has_no_rate_or_cost(
    mariadb_client: MariaDBClient,
) -> None:
    # Given the only gas rate ended on 1 November with no rate after it
    _seed_gas_rate(mariadb_client)
    with mariadb_client.session_write_scope() as session:
        session.query(model.product_rate).update({"valid_to": datetime(2026, 11, 1)})
    _seed_polls(mariadb_client, START, on=ON_POLLS)

    _job(mariadb_client).refresh(NOW)

    [row] = _heating_intervals(mariadb_client)
    assert row.unit_rate is None
    assert row.cost_gbp is None


def test_a_metered_half_hour_with_no_gas_rate_in_force_keeps_its_gas_and_has_no_cost(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_polls(mariadb_client, START, on=ON_POLLS)
    _seed_gas(mariadb_client, START, 2.25)

    _job(mariadb_client).refresh(NOW)

    [row] = _heating_intervals(mariadb_client)
    assert row.source == "measured"
    assert row.heating_kwh == pytest.approx(2.25)
    assert row.cost_gbp is None


def test_a_learned_boiler_kw_of_zero_falls_back_to_the_default(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a heating-off 12:00 half-hour that used 0.25 kWh on ten earlier days, and
    # 18:00 heating half-hours on the same days that metered only 0.2 kWh, less than
    # that hot-water baseline, so their heating works out at zero
    _seed_gas_rate(mariadb_client)
    for back in range(10, 0, -1):
        quiet = START.replace(hour=12) - timedelta(days=back)
        _seed_polls(mariadb_client, quiet, on=0)
        _seed_gas(mariadb_client, quiet, 0.25)
        busy = START - timedelta(days=back)
        _seed_polls(mariadb_client, busy, on=10)
        _seed_gas(mariadb_client, busy, 0.2)
        _job(mariadb_client).refresh(busy.replace(hour=23, tzinfo=UTC))
    _seed_polls(mariadb_client, START, on=ON_POLLS)

    _job(mariadb_client).refresh(NOW)

    # Then the unmetered half-hour is estimated at the 12 kW default, not at zero
    row = _heating_intervals(mariadb_client)[-1]
    assert row.source == "estimated"
    assert row.heating_kwh == pytest.approx(ON_POLLS_KWH_AT_12KW)


def test_a_run_after_a_stop_part_way_through_a_day_fills_the_rest_of_that_day(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a job that stopped at 10:30 nine days ago, so only that day's 10:00
    # half-hour was stored, though hive-app went on polling through the day
    _seed_gas_rate(mariadb_client)
    stop_day = START.replace(hour=10) - timedelta(days=9)
    for step in range(3):
        _seed_polls(mariadb_client, stop_day + HALF_HOUR * step, on=ON_POLLS)
    _job(mariadb_client).refresh(stop_day.replace(minute=30, tzinfo=UTC))
    assert [r.starts_at for r in _heating_intervals(mariadb_client)] == [stop_day]
    for back in range(8, 0, -1):
        _seed_polls(mariadb_client, START - timedelta(days=back), on=ON_POLLS)

    # When the job runs again
    _job(mariadb_client).refresh(NOW)

    # Then the rest of the stop day has its rows too
    starts = [row.starts_at for row in _heating_intervals(mariadb_client)]
    assert starts[:3] == [stop_day + HALF_HOUR * step for step in range(3)]
    assert len(starts) == 3 + 8


def test_a_zero_gas_tail_of_a_day_resumed_part_way_is_still_metered(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a job that stopped after storing the 18:30 half-hour of a London day
    # that used 0.25 kWh in each of its 18:00 and 18:30 half-hours and nothing after
    _seed_gas_rate(mariadb_client)
    stop_day = START - timedelta(days=9)
    for step in range(4):
        _seed_polls(mariadb_client, stop_day + HALF_HOUR * step, on=0)
        _seed_gas(mariadb_client, stop_day + HALF_HOUR * step, 0.25 * (step < 2))
    _job(mariadb_client).refresh(stop_day.replace(hour=19, minute=0, tzinfo=UTC))
    for back in range(8, 0, -1):
        _seed_polls(mariadb_client, START - timedelta(days=back), on=ON_POLLS)

    # When the job runs again
    _job(mariadb_client).refresh(NOW)

    # Then the 19:00 and 19:30 half-hours are metered at zero, not mistaken for
    # missing gas
    tail = _heating_intervals(mariadb_client)[2:4]
    assert [r.starts_at for r in tail] == [
        stop_day + HALF_HOUR * 2,
        stop_day + HALF_HOUR * 3,
    ]
    assert [(r.source, r.measured_kwh) for r in tail] == [("measured", 0.0)] * 2


WARM_UP_KW = 18.0
STEADY_KW = 6.0


def _kwh_at(warm_up_minutes: int, steady_minutes: int) -> float:
    return warm_up_minutes / 60 * WARM_UP_KW + steady_minutes / 60 * STEADY_KW


def _record_two_phase_runs(
    client: MariaDBClient, days: int, last_ten_minutes_kwh: float = _kwh_at(0, 10)
) -> None:
    """`days` earlier days, each with a 40-minute heating run from 18:00 whose gas
    comes from an 18 kW warm-up and a 6 kW steady draw: the 18:00 half-hour holds
    the run's first 20 minutes and 10 more, the 18:30 half-hour its last 10. The
    job runs at the end of each day."""
    for back in range(days, 0, -1):
        first = START - timedelta(days=back)
        second = first + HALF_HOUR
        _seed_polls(client, first, on=FULL_INTERVAL_POLLS)
        _seed_polls(client, second, on=5)
        _seed_gas(client, first, _kwh_at(20, 10))
        _seed_gas(client, second, last_ten_minutes_kwh)
        _job(client).refresh(first.replace(hour=23, minute=59, tzinfo=UTC))


def _seed_poll_states(client: MariaDBClient, start: datetime, states: str) -> None:
    """Two-minute polls from `start`, one per character: "1" with the thermostat
    calling for heat, "0" without."""
    rows = [
        {
            "polled_at": start + timedelta(minutes=2 * i),
            "current_temp": 19.0,
            "target_temp": 21.0 if state == "1" else 7.0,
            "mode": "SCHEDULE",
            "state": "ON" if state == "1" else "OFF",
            "boost_active": False,
        }
        for i, state in enumerate(states)
    ]
    with client.session_write_scope() as session:
        session.execute(heating_status_table.insert(), rows)


def _row_at(client: MariaDBClient, starts_at: datetime) -> model.heating_interval:
    return next(r for r in _heating_intervals(client) if r.starts_at == starts_at)


def test_a_half_hour_holding_a_runs_first_20_minutes_is_estimated_at_the_warm_up_kw(
    mariadb_client: MariaDBClient,
) -> None:
    # Given ten measured heating half-hours from five days of runs drawing 18 kW in
    # their first 20 minutes and 6 kW after
    _seed_gas_rate(mariadb_client)
    _record_two_phase_runs(mariadb_client, days=5)
    # and a run starting at 18:00 today, on for 12 minutes, whose gas has not arrived
    _seed_polls(mariadb_client, START, on=ON_POLLS)

    _job(mariadb_client).refresh(NOW)

    # Then all 12 minutes are in the warm-up, so it is estimated at 18 kW
    row = _heating_intervals(mariadb_client)[-1]
    assert row.source == "estimated"
    assert row.heating_kwh == pytest.approx(_kwh_at(12, 0))
    assert row.cost_gbp == pytest.approx(_kwh_at(12, 0) * GAS_UNIT_RATE_P / 100)


def test_a_half_hour_40_minutes_into_a_run_is_estimated_at_the_steady_kw(
    mariadb_client: MariaDBClient,
) -> None:
    # Given the learned 18 kW warm-up and 6 kW steady draw, and a run today from
    # 17:20 that is still on for the first 12 minutes of the 18:00 half-hour
    _seed_gas_rate(mariadb_client)
    _record_two_phase_runs(mariadb_client, days=5)
    _seed_poll_states(mariadb_client, START - HALF_HOUR * 2, "0" * 10 + "1" * 5)
    _seed_poll_states(mariadb_client, START - HALF_HOUR, "1" * 15)
    _seed_poll_states(mariadb_client, START, "1" * 6 + "0" * 9)

    _job(mariadb_client).refresh(NOW)

    # Then those 12 minutes are all past the warm-up, so they are priced at 6 kW
    row = _row_at(mariadb_client, START)
    assert row.early_on_minutes == 0
    assert row.heating_kwh == pytest.approx(_kwh_at(0, 12))


def test_only_a_runs_first_20_minutes_count_as_early_across_a_half_hour_boundary(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a run from 17:50 to 18:20
    _seed_poll_states(mariadb_client, START - HALF_HOUR, "0" * 10 + "1" * 5)
    _seed_poll_states(mariadb_client, START, "1" * 10 + "0" * 5)

    _job(mariadb_client).refresh(NOW)

    # Then its 10 minutes before 18:00 and the next 10 after it are early, and the
    # last 10 are not
    before = _row_at(mariadb_client, START - HALF_HOUR)
    after = _row_at(mariadb_client, START)
    assert (before.on_minutes, before.early_on_minutes) == (10, 10)
    assert (after.on_minutes, after.early_on_minutes) == (20, 10)


def test_a_single_off_poll_inside_a_run_does_not_start_a_new_warm_up(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a run from 18:00 with one OFF poll at 18:20 and on again to 18:28
    _seed_poll_states(mariadb_client, START, "1" * 10 + "0" + "1" * 4)

    _job(mariadb_client).refresh(NOW)

    # Then the minutes after the OFF poll are past the run's warm-up
    row = _row_at(mariadb_client, START)
    assert (row.on_minutes, row.early_on_minutes) == (28, 20)


def test_a_single_off_poll_a_few_seconds_late_does_not_start_a_new_warm_up(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a run from 18:00 whose polls drift: ON every 2 minutes to 18:18, one OFF
    # poll, then ON again from 18:22:05, just over 4 minutes after the last ON poll
    _seed_poll_states(mariadb_client, START, "1" * 10 + "0")
    _seed_poll_states(mariadb_client, START + timedelta(minutes=22, seconds=5), "1" * 4)

    _job(mariadb_client).refresh(NOW)

    # Then the minutes after the OFF poll are still past the run's warm-up
    row = _row_at(mariadb_client, START)
    assert (row.on_minutes, row.early_on_minutes) == (28, 20)


def test_two_off_polls_in_a_row_end_a_run_so_the_next_on_poll_warms_up_again(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a run from 18:00 to 18:16, OFF at 18:18 and 18:20, and on again to 18:28
    _seed_poll_states(mariadb_client, START, "1" * 9 + "00" + "1" * 4)

    _job(mariadb_client).refresh(NOW)

    # Then the second run's minutes are a fresh warm-up, so every minute is early
    row = _row_at(mariadb_client, START)
    assert (row.on_minutes, row.early_on_minutes) == (26, 26)


def test_a_run_from_before_the_recompute_window_is_split_by_its_real_start(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a run from 23:40 to 00:20 across the midnight that starts a London day
    # the job computes on its own
    midnight = datetime(2026, 10, 30, 0, 0)
    _seed_poll_states(mariadb_client, midnight - HALF_HOUR, "0" * 5 + "1" * 10)
    _seed_poll_states(mariadb_client, midnight, "1" * 10 + "0" * 5)

    _job(mariadb_client).refresh(NOW)

    # Then the 00:00 half-hour's minutes are 20 to 40 minutes into the run, not early
    row = _row_at(mariadb_client, midnight)
    assert (row.on_minutes, row.early_on_minutes) == (20, 0)


def test_history_implying_a_steady_kw_of_zero_falls_back_to_the_single_kw(
    mariadb_client: MariaDBClient,
) -> None:
    # Given five days of runs whose last 10 minutes used no gas at all, so the
    # steady draw works out at zero; together they used 35 kWh over 200 minutes on,
    # a single 10.5 kW
    _seed_gas_rate(mariadb_client)
    _record_two_phase_runs(mariadb_client, days=5, last_ten_minutes_kwh=0.0)
    _seed_polls(mariadb_client, START, on=ON_POLLS)

    _job(mariadb_client).refresh(NOW)

    # Then today's 12 warm-up minutes are estimated at the single 10.5 kW, not at
    # the 21 kW warm-up the history would teach
    row = _row_at(mariadb_client, START)
    assert row.source == "estimated"
    assert row.heating_kwh == pytest.approx(12 / 60 * 10.5)


def test_an_estimated_half_hours_heating_estimate_is_its_estimated_kwh(
    mariadb_client: MariaDBClient,
) -> None:
    # Given the learned 18 kW warm-up and 6 kW steady draw, and a heating half-hour
    # today with no gas yet
    _seed_gas_rate(mariadb_client)
    _record_two_phase_runs(mariadb_client, days=5)
    _seed_polls(mariadb_client, START, on=ON_POLLS)

    _job(mariadb_client).refresh(NOW)

    # Then its Heating Estimate is the kWh it is estimated at
    row = _row_at(mariadb_client, START)
    assert row.estimated_heating_kwh == pytest.approx(_kwh_at(12, 0))


def test_a_half_hour_keeps_its_earlier_heating_estimate_when_its_gas_arrives(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a heating half-hour estimated at the 12 kW default before any history
    _seed_gas_rate(mariadb_client)
    _seed_polls(mariadb_client, START, on=ON_POLLS)
    _job(mariadb_client).refresh(NOW)
    # and then history that teaches an 18 kW warm-up, which would estimate it higher
    _record_two_phase_runs(mariadb_client, days=5)

    # When its gas arrives and the job runs again
    _seed_gas(mariadb_client, START, 3.0)
    _job(mariadb_client).refresh(NOW)

    # Then it is measured and keeps the estimate made before the gas arrived
    row = _row_at(mariadb_client, START)
    assert row.source == "measured"
    assert row.estimated_heating_kwh == pytest.approx(ON_POLLS_KWH_AT_12KW)


def test_a_half_hour_measured_on_its_first_computation_gets_this_runs_estimate(
    mariadb_client: MariaDBClient,
) -> None:
    # Given the learned 18 kW warm-up and 6 kW steady draw, and a heating half-hour
    # whose gas is already there when the job first computes it
    _seed_gas_rate(mariadb_client)
    _record_two_phase_runs(mariadb_client, days=5)
    _seed_polls(mariadb_client, START, on=ON_POLLS)
    _seed_gas(mariadb_client, START, _kwh_at(12, 0))

    _job(mariadb_client).refresh(NOW)

    # Then its Heating Estimate comes from this run's Warm-up kW
    row = _row_at(mariadb_client, START)
    assert row.source == "measured"
    assert row.estimated_heating_kwh == pytest.approx(_kwh_at(12, 0))


def test_a_heating_off_half_hours_heating_estimate_is_zero(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a half-hour with the heating off whose hot-water gas is metered
    _seed_gas_rate(mariadb_client)
    _seed_polls(mariadb_client, START, on=0)
    _seed_gas(mariadb_client, START, 0.5)

    _job(mariadb_client).refresh(NOW)

    row = _row_at(mariadb_client, START)
    assert row.estimated_heating_kwh == 0
