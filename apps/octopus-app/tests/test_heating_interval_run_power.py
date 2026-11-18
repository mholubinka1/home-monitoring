"""Heating Intervals estimated at a Warm-up and a Steady kW per Heating Run, their
frozen Heating Estimate, and the one-time fill of rows stored before (ADR-0039)."""

from datetime import UTC, datetime, timedelta

import pytest
from tests.test_heating_interval_job import (
    FULL_INTERVAL_POLLS,
    GAS_UNIT_RATE_P,
    HALF_HOUR,
    NOW,
    ON_POLLS,
    ON_POLLS_KWH_AT_12KW,
    START,
    _heating_intervals,
    _job,
    _seed_gas,
    _seed_gas_rate,
    _seed_polls,
)

from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient, heating_status_table

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


PRE_CHANGE_COMPUTED_AT = datetime(2026, 10, 20, 3, 0)


def _store_pre_change_row(
    client: MariaDBClient, starts_at: datetime, on_minutes: int, kwh: float
) -> None:
    """A measured half-hour stored before this change: no early on-minutes and no
    Heating Estimate."""
    with client.session_write_scope() as session:
        session.add(
            model.heating_interval(
                starts_at=starts_at,
                ends_at=starts_at + HALF_HOUR,
                poll_count=FULL_INTERVAL_POLLS,
                on_minutes=on_minutes,
                early_on_minutes=None,
                measured_kwh=kwh,
                heating_kwh=kwh,
                source="measured",
                unit_rate=GAS_UNIT_RATE_P,
                cost_gbp=kwh * GAS_UNIT_RATE_P / 100,
                estimated_heating_kwh=None,
                computed_at=PRE_CHANGE_COMPUTED_AT,
            )
        )


def _store_pre_change_run_across_18_00(client: MariaDBClient, day: datetime) -> None:
    """A run from 17:50 to 18:20 on `day`, its polls kept and its two half-hours
    stored before this change."""
    _seed_poll_states(client, day - HALF_HOUR, "0" * 10 + "1" * 5)
    _seed_poll_states(client, day, "1" * 10 + "0" * 5)
    _store_pre_change_row(client, day - HALF_HOUR, on_minutes=10, kwh=3.0)
    _store_pre_change_row(client, day, on_minutes=20, kwh=4.0)


def test_half_hours_stored_before_the_change_get_their_early_minutes_from_polls(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a run from 17:50 to 18:20 ten days ago, stored before this change
    day = START - timedelta(days=10)
    _store_pre_change_run_across_18_00(mariadb_client, day)

    _job(mariadb_client).refresh(NOW)

    # Then its half-hours get early on-minutes from the kept polls
    assert _row_at(mariadb_client, day - HALF_HOUR).early_on_minutes == 10
    assert _row_at(mariadb_client, day).early_on_minutes == 10


def test_filling_early_minutes_changes_nothing_else_on_the_stored_half_hours(
    mariadb_client: MariaDBClient,
) -> None:
    day = START - timedelta(days=10)
    _store_pre_change_run_across_18_00(mariadb_client, day)

    _job(mariadb_client).refresh(NOW)

    # Then its measured figures, cost, source and time of computing are as stored,
    # and it has no Heating Estimate
    row = _row_at(mariadb_client, day)
    assert (row.measured_kwh, row.heating_kwh) == (4.0, 4.0)
    assert row.cost_gbp == pytest.approx(4.0 * GAS_UNIT_RATE_P / 100)
    assert row.source == "measured"
    assert row.computed_at == PRE_CHANGE_COMPUTED_AT
    assert row.estimated_heating_kwh is None


def test_the_first_run_learns_warm_up_and_steady_kw_from_half_hours_stored_before(
    mariadb_client: MariaDBClient,
) -> None:
    # Given ten measured heating half-hours stored before this change, from runs
    # five to nine days ago drawing 18 kW in their first 20 minutes and 6 kW after,
    # with their polls kept
    _seed_gas_rate(mariadb_client)
    for back in range(9, 4, -1):
        first = START - timedelta(days=back)
        _seed_polls(mariadb_client, first, on=FULL_INTERVAL_POLLS)
        _seed_polls(mariadb_client, first + HALF_HOUR, on=5)
        _store_pre_change_row(mariadb_client, first, 30, _kwh_at(20, 10))
        _store_pre_change_row(mariadb_client, first + HALF_HOUR, 10, _kwh_at(0, 10))
    # and a run starting at 18:00 today, on for 12 minutes, whose gas has not arrived
    _seed_polls(mariadb_client, START, on=ON_POLLS)

    # When the job first runs after the change
    _job(mariadb_client).refresh(NOW)

    # Then today's half-hour is estimated at the 18 kW Warm-up kW
    row = _row_at(mariadb_client, START)
    assert row.source == "estimated"
    assert row.heating_kwh == pytest.approx(_kwh_at(12, 0))


def test_a_half_hour_stored_before_the_change_without_polls_does_not_stop_the_job(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a half-hour stored before this change whose polls are gone
    _store_pre_change_row(mariadb_client, START - timedelta(days=10), 20, 4.0)
    # and a heating half-hour today
    _seed_polls(mariadb_client, START, on=ON_POLLS)

    _job(mariadb_client).refresh(NOW)

    # Then today's half-hour is still computed, and the old one is left without
    # early on-minutes
    assert _row_at(mariadb_client, START).on_minutes == 12
    assert _row_at(mariadb_client, START - timedelta(days=10)).early_on_minutes is None


def test_with_every_half_hour_already_split_the_fill_reads_no_polls(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given half-hours ten days ago and yesterday that already have their split
    _store_pre_change_run_across_18_00(mariadb_client, START - timedelta(days=10))
    _seed_polls(mariadb_client, START - timedelta(days=1), on=ON_POLLS)
    _job(mariadb_client).refresh(NOW - timedelta(days=1) + timedelta(hours=1))
    _job(mariadb_client).refresh(NOW)
    # and a record of every span of polls the job reads from now on
    spans: list[tuple[datetime, datetime]] = []
    read_polls = mariadb_client.read_heating_polls

    def recording_read(start: datetime, end: datetime) -> list:
        spans.append((start, end))
        return read_polls(start, end)

    monkeypatch.setattr(mariadb_client, "read_heating_polls", recording_read)

    # When the job runs again
    _job(mariadb_client).refresh(NOW)

    # Then it reads only the polls of its own four-day window
    window_start = datetime(2026, 10, 30, 0, 0)
    assert spans
    assert all(start >= window_start - timedelta(minutes=25) for start, _ in spans)
