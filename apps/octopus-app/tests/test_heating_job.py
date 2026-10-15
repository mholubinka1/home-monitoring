from datetime import UTC, date, datetime, timedelta

import pytest

from octopus_app.data.heating_job import HeatingModelJob
from octopus_app.data.heating_model import THRESHOLDS, WEIGHT_MAX, UnusableReason
from octopus_app.data.heating_weeks import WeekStatus
from octopus_app.data.model import HeatingModelRecord
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient

from .heating_history_fixtures import (
    PLANTED_BASELOAD,
    PLANTED_SLOPE,
    PLANTED_THRESHOLD,
    effective_of,
    seed_forecast_means,
    seed_gas_day,
    seed_heating_readings,
    seed_holiday,
    seed_planted_history,
    seed_weather_day,
)

LAST_GAS_DAY = date(2026, 10, 12)
TODAY = date(2026, 10, 13)
NOW = datetime(2026, 10, 13, 4, 0, tzinfo=UTC)  # the 04:00 daily job, next morning


def _model_count(client: MariaDBClient) -> int:
    with client.session_read_scope() as session:
        return session.query(model.heating_model).count()


def test_the_first_refresh_stores_a_usable_model_close_to_the_planted_one(
    mariadb_client: MariaDBClient,
) -> None:
    seed_planted_history(mariadb_client, last_day=LAST_GAS_DAY, days=730)

    HeatingModelJob(mariadb_client).refresh(now=NOW)

    stored = mariadb_client.read_latest_heating_model()
    assert stored is not None
    assert stored.model.usable
    assert stored.model.window_end == LAST_GAS_DAY
    assert stored.model.threshold == pytest.approx(PLANTED_THRESHOLD, abs=0.6)
    assert stored.model.slope == pytest.approx(PLANTED_SLOPE, abs=0.5)


def test_the_refresh_writes_verdicts_forecast_and_weeks_tied_to_the_stored_model(
    mariadb_client: MariaDBClient,
) -> None:
    _, gas = seed_planted_history(mariadb_client, last_day=LAST_GAS_DAY, days=730)
    seed_forecast_means(
        mariadb_client, {TODAY + timedelta(days=i): 6.0 + i for i in range(7)}
    )

    HeatingModelJob(mariadb_client).refresh(now=NOW)

    stored = mariadb_client.read_latest_heating_model()
    assert stored is not None
    verdicts = mariadb_client.read_heating_day_verdicts(date(2020, 1, 1), TODAY)
    assert {day.date for _, day in verdicts} == set(gas)
    assert {model_id for model_id, _ in verdicts} == {stored.id}
    for _, day in verdicts:
        slices = (
            day.baseload
            + day.expected_heating
            + day.normal_variation
            + day.possible
            + day.clear
        )
        assert slices == pytest.approx(day.actual_gas)
    forecast = mariadb_client.read_heating_forecast_days()
    assert [row.date for _, row in forecast] == [
        TODAY + timedelta(days=i) for i in range(7)
    ]
    assert {model_id for model_id, _ in forecast} == {stored.id}
    for _, row in forecast:
        assert row.expected_gas == pytest.approx(
            stored.model.baseload
            + stored.model.slope
            * max(0.0, stored.model.threshold - row.effective_temperature)
        )
    weeks = mariadb_client.read_heating_weeks()
    assert weeks
    assert {model_id for model_id, _ in weeks} == {stored.id}
    assert {week.status for _, week in weeks} <= set(WeekStatus)


def test_a_model_fitted_under_seven_days_ago_is_not_refitted_but_rejudges(
    mariadb_client: MariaDBClient,
) -> None:
    means, _ = seed_planted_history(mariadb_client, last_day=LAST_GAS_DAY, days=730)
    job = HeatingModelJob(mariadb_client)
    job.refresh(now=NOW)
    seed_weather_day(mariadb_client, TODAY, means[LAST_GAS_DAY])
    seed_gas_day(mariadb_client, TODAY, 20.0)

    job.refresh(now=NOW + timedelta(days=1))

    assert _model_count(mariadb_client) == 1
    verdict_days = {
        day.date for _, day in mariadb_client.read_heating_day_verdicts(TODAY, TODAY)
    }
    assert verdict_days == {TODAY}


@pytest.mark.parametrize(
    ("age", "refits"),
    [
        (timedelta(days=7), True),
        # A run a few seconds early still counts as seven local days on.
        (timedelta(days=7) - timedelta(seconds=3), True),
        # NOW is 05:00 local (BST), so local midnight, seven dates on, is 6d19h later.
        (timedelta(days=6, hours=19), True),
        (timedelta(days=6, hours=18, minutes=59), False),  # 23:59, only six dates on
        (timedelta(days=6), False),
    ],
)
def test_the_model_is_refitted_once_it_is_seven_local_days_old(
    mariadb_client: MariaDBClient, age: timedelta, refits: bool
) -> None:
    seed_planted_history(mariadb_client, last_day=LAST_GAS_DAY, days=730)
    job = HeatingModelJob(mariadb_client)
    job.refresh(now=NOW)

    job.refresh(now=NOW + age)

    assert _model_count(mariadb_client) == (2 if refits else 1)
    latest = mariadb_client.read_latest_heating_model()
    assert latest is not None
    verdict_models = {
        model_id
        for model_id, _ in mariadb_client.read_heating_day_verdicts(
            date(2020, 1, 1), TODAY
        )
    }
    assert verdict_models == {latest.id}


def test_an_unusable_fit_stores_the_reason_and_writes_no_rows(
    mariadb_client: MariaDBClient,
) -> None:
    seed_planted_history(mariadb_client, last_day=LAST_GAS_DAY, days=60)

    HeatingModelJob(mariadb_client).refresh(now=NOW)

    stored = mariadb_client.read_latest_heating_model()
    assert stored is not None
    assert not stored.model.usable
    assert stored.model.unusable_reason == UnusableReason.TOO_FEW_DAYS.value
    assert stored.model.label == "estimated"
    assert mariadb_client.read_heating_day_verdicts(date(2020, 1, 1), TODAY) == []
    assert mariadb_client.read_heating_forecast_days() == []
    assert mariadb_client.read_heating_weeks() == []


def test_a_later_unusable_refit_is_recorded_and_the_day_is_rejudged_with_the_old_model(
    mariadb_client: MariaDBClient,
) -> None:
    seed_planted_history(mariadb_client, last_day=LAST_GAS_DAY, days=730)
    job = HeatingModelJob(mariadb_client)
    job.refresh(now=NOW)
    first = mariadb_client.read_latest_heating_model()
    assert first is not None
    with mariadb_client.session_write_scope() as session:
        session.query(model.daily_consumption_summary).filter(
            model.daily_consumption_summary.date < LAST_GAS_DAY - timedelta(days=40)
        ).delete()

    job.refresh(now=NOW + timedelta(days=8))

    latest = mariadb_client.read_latest_heating_model()
    assert latest is not None
    assert latest.id != first.id
    assert not latest.model.usable
    verdicts = mariadb_client.read_heating_day_verdicts(date(2020, 1, 1), TODAY)
    assert 0 < len(verdicts) <= 41  # re-judged on what gas is left
    assert {model_id for model_id, _ in verdicts} == {first.id}
    assert {model_id for model_id, _ in mariadb_client.read_heating_weeks()} == {
        first.id
    }
    forecast_model_ids = {m for m, _ in mariadb_client.read_heating_forecast_days()}
    assert forecast_model_ids <= {first.id}


def test_a_first_fit_that_is_unusable_is_retried_the_next_day_with_more_data(
    mariadb_client: MariaDBClient,
) -> None:
    _, gas = seed_planted_history(mariadb_client, last_day=LAST_GAS_DAY, days=730)
    older = {d: g for d, g in gas.items() if d < LAST_GAS_DAY - timedelta(days=100)}
    with mariadb_client.session_write_scope() as session:
        session.query(model.daily_consumption_summary).filter(
            model.daily_consumption_summary.date < LAST_GAS_DAY - timedelta(days=100)
        ).delete()
    job = HeatingModelJob(mariadb_client)
    job.refresh(now=NOW)
    first = mariadb_client.read_latest_heating_model()
    assert first is not None
    assert not first.model.usable
    for day, kwh in older.items():
        seed_gas_day(mariadb_client, day, kwh)

    job.refresh(now=NOW + timedelta(days=1))

    latest = mariadb_client.read_latest_heating_model()
    assert latest is not None
    assert latest.model.usable
    assert _model_count(mariadb_client) == 2
    verdict_models = {
        m for m, _ in mariadb_client.read_heating_day_verdicts(date(2020, 1, 1), TODAY)
    }
    assert verdict_models == {latest.id}


def test_a_stored_model_with_no_measurable_baseload_judges_nothing_and_does_not_raise(
    mariadb_client: MariaDBClient,
) -> None:
    seed_planted_history(mariadb_client, last_day=LAST_GAS_DAY, days=730)
    job = HeatingModelJob(mariadb_client)
    job.refresh(now=NOW)
    with mariadb_client.session_write_scope() as session:
        # Only winter days left: no warm day, so no baseload can be measured.
        session.query(model.daily_consumption_summary).filter(
            model.daily_consumption_summary.date.notin_(
                [date(2026, 1, 10) + timedelta(days=i) for i in range(5)]
            )
        ).delete(synchronize_session=False)

    job.refresh(now=NOW + timedelta(days=1))

    assert _model_count(mariadb_client) == 1


def test_a_stored_model_whose_weight_is_at_the_cap_still_judges_and_projects(
    mariadb_client: MariaDBClient,
) -> None:
    # The live fit lands on the 0.8 cap; the next day's run reads it back from
    # the table and must not trip the weight's range check.
    seed_planted_history(mariadb_client, last_day=LAST_GAS_DAY, days=730)
    seed_forecast_means(mariadb_client, {TODAY: 6.0})
    mariadb_client.write_heating_model(
        HeatingModelRecord(
            fitted_at=NOW,
            window_start=date(2025, 10, 13),
            window_end=LAST_GAS_DAY,
            usable=True,
            unusable_reason=None,
            days_used=333,
            threshold=PLANTED_THRESHOLD,
            upper_threshold=PLANTED_THRESHOLD + 1,
            slope=PLANTED_SLOPE,
            weight=WEIGHT_MAX,
            r_squared=0.9,
            baseload=PLANTED_BASELOAD,
            baseload_measured_days=1,
            baseload_interpolated_days=0,
            baseload_held_days=0,
            noise_base=2.0,
            noise_per_degree_day=0.1,
            noise_floor=1.0,
            thermostat_threshold=None,
            heating_days=None,
            non_heating_days=None,
            label="estimated",
        )
    )

    HeatingModelJob(mariadb_client).refresh(now=NOW)  # the model is not stale

    assert _model_count(mariadb_client) == 1
    assert mariadb_client.read_heating_day_verdicts(date(2020, 1, 1), TODAY)
    assert [row.date for _, row in mariadb_client.read_heating_forecast_days()] == [
        TODAY
    ]


def test_no_usable_gas_days_writes_nothing_and_does_not_raise(
    mariadb_client: MariaDBClient,
) -> None:
    HeatingModelJob(mariadb_client).refresh(now=NOW)

    assert _model_count(mariadb_client) == 0


def test_a_holiday_is_judged_away_and_does_not_pull_the_baseload_down(
    mariadb_client: MariaDBClient,
) -> None:
    seed_planted_history(mariadb_client, last_day=LAST_GAS_DAY, days=730)
    holiday_start = date(2026, 6, 29)  # a Monday
    seed_holiday(mariadb_client, holiday_start, 14, 0.5)

    HeatingModelJob(mariadb_client).refresh(now=NOW)

    stored = mariadb_client.read_latest_heating_model()
    assert stored is not None
    assert stored.model.baseload == pytest.approx(PLANTED_BASELOAD, abs=0.5)
    holiday = [
        day
        for _, day in mariadb_client.read_heating_day_verdicts(
            holiday_start, holiday_start + timedelta(days=13)
        )
    ]
    assert len(holiday) == 14
    assert all(day.away and not day.needed for day in holiday)
    holiday_weeks = [
        week
        for _, week in mariadb_client.read_heating_weeks()
        if week.week_start in (holiday_start, holiday_start + timedelta(days=7))
    ]
    assert [week.complete_days for week in holiday_weeks] == [0, 0]


def test_only_forecast_days_with_a_mean_get_a_row_and_use_yesterdays_observed_mean(
    mariadb_client: MariaDBClient,
) -> None:
    means, _ = seed_planted_history(mariadb_client, last_day=LAST_GAS_DAY, days=730)
    seed_forecast_means(
        mariadb_client,
        {TODAY: 5.0, TODAY + timedelta(days=1): 7.0, TODAY + timedelta(days=3): 9.0},
    )

    HeatingModelJob(mariadb_client).refresh(now=NOW)

    stored = mariadb_client.read_latest_heating_model()
    assert stored is not None
    forecast = {row.date: row for _, row in mariadb_client.read_heating_forecast_days()}
    assert set(forecast) == {TODAY, TODAY + timedelta(days=1)}
    weight = stored.model.weight
    assert forecast[TODAY].effective_temperature == pytest.approx(
        (1 - weight) * 5.0 + weight * means[LAST_GAS_DAY]
    )


def test_the_upper_threshold_is_on_the_grid_and_borderline_days_are_needed(
    mariadb_client: MariaDBClient,
) -> None:
    seed_planted_history(mariadb_client, last_day=LAST_GAS_DAY, days=730)

    HeatingModelJob(mariadb_client).refresh(now=NOW)

    stored = mariadb_client.read_latest_heating_model()
    assert stored is not None
    threshold, upper = stored.model.threshold, stored.model.upper_threshold
    assert upper in THRESHOLDS
    assert upper >= threshold
    assert upper > threshold
    borderline = [
        day
        for _, day in mariadb_client.read_heating_day_verdicts(date(2020, 1, 1), TODAY)
        if day.effective_temp is not None
        and threshold <= day.effective_temp < upper
        and not day.away
    ]
    assert borderline
    assert all(day.needed for day in borderline)


def test_without_heating_status_history_the_model_is_only_estimated(
    mariadb_client: MariaDBClient,
) -> None:
    seed_planted_history(mariadb_client, last_day=LAST_GAS_DAY, days=730)

    HeatingModelJob(mariadb_client).refresh(now=NOW)

    stored = mariadb_client.read_latest_heating_model()
    assert stored is not None
    assert stored.model.label == "estimated"
    assert stored.model.thermostat_threshold is None
    assert stored.model.heating_days is None
    assert stored.model.non_heating_days is None


def test_thermostat_readings_agreeing_with_the_gas_threshold_confirm_the_model(
    mariadb_client: MariaDBClient,
) -> None:
    means, _ = seed_planted_history(mariadb_client, last_day=LAST_GAS_DAY, days=730)
    recent = [LAST_GAS_DAY - timedelta(days=i) for i in range(1, 300)]
    cold = sorted(
        (d for d in recent if effective_of(means, d) < 11.0),
        key=lambda d: -effective_of(means, d),
    )[:20]
    warm = sorted(
        (d for d in recent if effective_of(means, d) > 13.0),
        key=lambda d: effective_of(means, d),
    )[:20]
    seed_heating_readings(
        mariadb_client, {**{d: True for d in cold}, **{d: False for d in warm}}
    )

    HeatingModelJob(mariadb_client).refresh(now=NOW)

    stored = mariadb_client.read_latest_heating_model()
    assert stored is not None
    assert stored.model.label == "confirmed"
    assert stored.model.heating_days == 20
    assert stored.model.non_heating_days == 20
    assert stored.model.thermostat_threshold == pytest.approx(
        stored.model.threshold, abs=1.5
    )


def test_the_job_is_not_ready_for_a_first_fit_once_a_model_exists(
    mariadb_client: MariaDBClient,
) -> None:
    seed_planted_history(mariadb_client, last_day=LAST_GAS_DAY, days=730)
    job = HeatingModelJob(mariadb_client)
    job.refresh(now=NOW)

    assert not job.ready_for_first_fit(now=NOW)


def test_the_job_is_not_ready_for_a_first_fit_with_too_few_gas_days(
    mariadb_client: MariaDBClient,
) -> None:
    seed_planted_history(mariadb_client, last_day=LAST_GAS_DAY, days=100)

    assert not HeatingModelJob(mariadb_client).ready_for_first_fit(now=NOW)


def test_the_job_is_ready_for_a_first_fit_with_enough_gas_days_and_no_model(
    mariadb_client: MariaDBClient,
) -> None:
    seed_planted_history(mariadb_client, last_day=LAST_GAS_DAY, days=200)

    assert HeatingModelJob(mariadb_client).ready_for_first_fit(now=NOW)
