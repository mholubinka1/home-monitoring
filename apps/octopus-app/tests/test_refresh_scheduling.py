import datetime
import threading
from datetime import datetime as dt
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
from schedule import Scheduler

from octopus_app.common.config import RefreshSettings
from octopus_app.data.agile_forecast import AgileForecastRetriever
from octopus_app.data.consumption import ConsumptionRetriever
from octopus_app.data.consumption_summary import (
    ConsumptionSummaryBackfill,
    ConsumptionSummaryRetriever,
)
from octopus_app.data.cost_forecast import CostForecastRetriever
from octopus_app.data.model import ConsumptionSummary, Energy
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.pricing import PricingRetriever
from octopus_app.data.pruning import DataPruner
from octopus_app.main import (
    register_agile_forecast_refresh_job,
    register_consumption_backfill_job,
    register_consumption_summary_job,
    register_cost_forecast_refresh_job,
    register_jobs,
    register_pricing_job,
    run_backfill_at_startup,
    run_initial_agile_forecast_sync,
    run_initial_consumption_summary_sync,
    run_initial_cost_forecast_sync,
    run_initial_pricing_sync,
    run_pending_safely,
)

REFRESH_CONFIG = RefreshSettings(refresh_interval=4, retention=45)


def test_registered_job_runs_on_the_configured_refresh_interval(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()

    job = register_jobs(
        scheduler, REFRESH_CONFIG, Mock(spec=ConsumptionRetriever), mariadb_client
    )

    assert job.interval == REFRESH_CONFIG.refresh_interval
    assert job.unit == "hours"


def test_successful_refresh_is_recorded_as_a_successful_job_run(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()
    consumption = Mock(spec=ConsumptionRetriever)

    job = register_jobs(scheduler, REFRESH_CONFIG, consumption, mariadb_client)
    worker = job.run()
    worker.join()

    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).all()

    assert len(runs) == 1
    assert runs[0].job_name == "consumption_refresh"
    assert runs[0].status == "success"
    assert runs[0].error_message is None


def test_persistently_failing_refresh_retries_with_exponential_backoff(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleep_delays: list[int] = []
    monkeypatch.setattr("octopus_app.common.decorator.time.sleep", sleep_delays.append)
    scheduler = Scheduler()
    consumption = Mock(spec=ConsumptionRetriever)
    consumption.refresh.side_effect = RuntimeError("Octopus API unavailable")

    job = register_jobs(scheduler, REFRESH_CONFIG, consumption, mariadb_client)
    job.run().join()

    assert sleep_delays == [60, 120, 240, 480]
    assert consumption.refresh.call_count == 5

    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).all()

    assert len(runs) == 5
    assert all(run.status == "failure" for run in runs)
    assert all(run.error_message == "Octopus API unavailable" for run in runs)


def test_a_second_invocation_is_skipped_while_a_worker_is_already_running(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()
    consumption = Mock(spec=ConsumptionRetriever)
    started = threading.Event()
    release = threading.Event()
    released_in_time: list[bool] = []

    def slow_refresh() -> None:
        started.set()
        released_in_time.append(release.wait(timeout=5))

    consumption.refresh.side_effect = slow_refresh

    job = register_jobs(scheduler, REFRESH_CONFIG, consumption, mariadb_client)
    first_worker = job.run()
    assert started.wait(timeout=5), "worker did not start within timeout"

    second_worker = job.run()

    release.set()
    first_worker.join()

    assert released_in_time == [True], "worker did not observe release within timeout"
    assert second_worker is first_worker
    assert consumption.refresh.call_count == 1


def test_a_new_invocation_after_the_worker_finished_starts_a_fresh_attempt_count(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("octopus_app.common.decorator.time.sleep", lambda seconds: None)
    scheduler = Scheduler()
    consumption = Mock(spec=ConsumptionRetriever)
    consumption.refresh.side_effect = RuntimeError("Octopus API unavailable")

    job = register_jobs(scheduler, REFRESH_CONFIG, consumption, mariadb_client)
    job.run().join()

    consumption.refresh.side_effect = None
    job.run().join()

    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).order_by(model.job_run.ran_at).all()

    assert [run.status for run in runs] == ["failure"] * 5 + ["success"]


def test_pricing_job_runs_on_the_configured_interval_and_records_its_outcome(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()
    pricing = Mock(spec=PricingRetriever)

    job = register_pricing_job(scheduler, REFRESH_CONFIG, pricing, mariadb_client)

    assert job.interval == REFRESH_CONFIG.refresh_interval
    assert job.unit == "hours"

    job.run().join()

    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).all()

    assert len(runs) == 1
    assert runs[0].job_name == "pricing_refresh"
    assert runs[0].status == "success"


def test_run_pending_safely_does_not_propagate_a_scheduled_job_failure() -> None:
    scheduler = Mock(spec=Scheduler)
    scheduler.run_pending.side_effect = RuntimeError("boom")

    run_pending_safely(scheduler)


def test_run_initial_pricing_sync_does_not_propagate_a_startup_failure() -> None:
    pricing = Mock(spec=PricingRetriever)
    pricing.refresh.side_effect = RuntimeError("Octopus API unavailable")

    run_initial_pricing_sync(pricing)


def test_consumption_summary_job_is_registered_for_monday_at_0400(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()

    job = register_consumption_summary_job(
        scheduler,
        Mock(spec=ConsumptionSummaryRetriever),
        Mock(spec=DataPruner),
        mariadb_client,
    )

    assert job.unit == "weeks"
    assert job.start_day == "monday"
    assert str(job.at_time) == "04:00:00"


def test_a_successful_consumption_summary_run_is_recorded_as_a_successful_job_run(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()
    consumption_summary = Mock(spec=ConsumptionSummaryRetriever)

    job = register_consumption_summary_job(
        scheduler, consumption_summary, Mock(spec=DataPruner), mariadb_client
    )
    job.run().join()

    with mariadb_client.session_read_scope() as session:
        runs = (
            session.query(model.job_run)
            .filter_by(job_name="update_consumption_summary")
            .all()
        )

    assert len(runs) == 1
    assert runs[0].status == "success"


def test_a_persistently_failing_consumption_summary_run_retries_with_backoff(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleep_delays: list[int] = []
    monkeypatch.setattr("octopus_app.common.decorator.time.sleep", sleep_delays.append)
    scheduler = Scheduler()
    consumption_summary = Mock(spec=ConsumptionSummaryRetriever)
    consumption_summary.refresh.side_effect = RuntimeError("MariaDB unavailable")
    pruner = Mock(spec=DataPruner)

    job = register_consumption_summary_job(
        scheduler, consumption_summary, pruner, mariadb_client
    )
    job.run().join()

    assert sleep_delays == [60, 120, 240, 480]
    assert consumption_summary.refresh.call_count == 5
    pruner.run.assert_not_called()

    with mariadb_client.session_read_scope() as session:
        summary_runs = (
            session.query(model.job_run)
            .filter_by(job_name="update_consumption_summary")
            .all()
        )
        prune_runs = (
            session.query(model.job_run).filter_by(job_name="prune_old_data").all()
        )

    assert len(summary_runs) == 5
    assert all(run.status == "failure" for run in summary_runs)
    assert all(run.error_message == "MariaDB unavailable" for run in summary_runs)
    assert len(prune_runs) == 1
    assert prune_runs[0].status == "skipped"


def test_run_initial_consumption_summary_sync_does_not_propagate_a_startup_failure() -> (
    None
):
    consumption_summary = Mock(spec=ConsumptionSummaryRetriever)
    consumption_summary.refresh.side_effect = RuntimeError("MariaDB unavailable")

    run_initial_consumption_summary_sync(consumption_summary)


def test_run_initial_cost_forecast_sync_does_not_propagate_a_startup_failure() -> None:
    cost_forecast = Mock(spec=CostForecastRetriever)
    cost_forecast.refresh.side_effect = RuntimeError("Kraken unavailable")

    run_initial_cost_forecast_sync(cost_forecast)


def test_cost_forecast_refresh_job_is_registered_hourly(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()

    job = register_cost_forecast_refresh_job(
        scheduler, Mock(spec=CostForecastRetriever), mariadb_client
    )

    assert job.interval == 1
    assert job.unit == "hours"


def test_a_successful_cost_forecast_run_is_recorded_as_a_successful_job_run(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()
    cost_forecast = Mock(spec=CostForecastRetriever)

    job = register_cost_forecast_refresh_job(scheduler, cost_forecast, mariadb_client)
    job.run().join()

    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).all()

    assert len(runs) == 1
    assert runs[0].job_name == "cost_forecast_refresh"
    assert runs[0].status == "success"


def test_a_failed_cost_forecast_run_is_recorded_as_a_failed_job_run(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This exercises the job-registration/backoff wrapper shared by every
    # job in this file, using a Mock retriever -- it does not touch
    # cost_forecast at all. CostForecastRetriever's own "no row written on
    # failure" behavior is covered separately, against a real MariaDBClient,
    # by the test_kraken_unreachable_* tests in test_cost_forecast_retriever.py.
    monkeypatch.setattr("octopus_app.common.decorator.time.sleep", lambda seconds: None)
    scheduler = Scheduler()
    cost_forecast = Mock(spec=CostForecastRetriever)
    cost_forecast.refresh.side_effect = RuntimeError("Kraken unavailable")

    job = register_cost_forecast_refresh_job(scheduler, cost_forecast, mariadb_client)
    job.run().join()

    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).all()

    assert all(run.status == "failure" for run in runs)
    assert len(runs) > 0


def test_agile_forecast_refresh_job_is_registered_hourly(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()

    job = register_agile_forecast_refresh_job(
        scheduler, Mock(spec=AgileForecastRetriever), mariadb_client
    )

    assert job.interval == 1
    assert job.unit == "hours"


def test_a_successful_agile_forecast_run_is_recorded_as_a_successful_job_run(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()
    agile_forecast = Mock(spec=AgileForecastRetriever)

    job = register_agile_forecast_refresh_job(scheduler, agile_forecast, mariadb_client)
    job.run().join()

    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).all()

    assert len(runs) == 1
    assert runs[0].job_name == "agile_forecast_refresh"
    assert runs[0].status == "success"


def test_a_failed_agile_forecast_run_is_recorded_as_a_failed_job_run(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("octopus_app.common.decorator.time.sleep", lambda seconds: None)
    scheduler = Scheduler()
    agile_forecast = Mock(spec=AgileForecastRetriever)
    agile_forecast.refresh.side_effect = RuntimeError(
        "agilepredict.com and x2r.uk both unavailable"
    )

    job = register_agile_forecast_refresh_job(scheduler, agile_forecast, mariadb_client)
    job.run().join()

    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).all()

    assert all(run.status == "failure" for run in runs)
    assert len(runs) > 0


def test_run_initial_agile_forecast_sync_does_not_propagate_a_startup_failure() -> None:
    agile_forecast = Mock(spec=AgileForecastRetriever)
    agile_forecast.refresh.side_effect = RuntimeError(
        "agilepredict.com and x2r.uk both unavailable"
    )

    run_initial_agile_forecast_sync(agile_forecast)


def test_pruning_runs_immediately_after_a_successful_summary_run_in_the_same_tick(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()
    consumption_summary = Mock(spec=ConsumptionSummaryRetriever)
    pruner = Mock(spec=DataPruner)

    job = register_consumption_summary_job(
        scheduler, consumption_summary, pruner, mariadb_client
    )
    job.run().join()

    pruner.run.assert_called_once()
    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).filter_by(job_name="prune_old_data").all()
    assert len(runs) == 1
    assert runs[0].status == "success"


def test_pruning_is_skipped_and_recorded_when_the_summary_run_ultimately_fails(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("octopus_app.common.decorator.time.sleep", lambda seconds: None)
    scheduler = Scheduler()
    consumption_summary = Mock(spec=ConsumptionSummaryRetriever)
    consumption_summary.refresh.side_effect = RuntimeError("MariaDB unavailable")
    pruner = Mock(spec=DataPruner)

    job = register_consumption_summary_job(
        scheduler, consumption_summary, pruner, mariadb_client
    )
    job.run().join()

    pruner.run.assert_not_called()
    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).filter_by(job_name="prune_old_data").all()
    assert len(runs) == 1
    assert runs[0].status == "skipped"


def test_pruning_reflects_this_cycles_outcome_not_a_stale_previous_success(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: prune_old_data must gate on *this* cycle's summary
    outcome, not whichever job_run happened to be most recently recorded.
    A prior cycle's success sitting in the table must not let pruning
    proceed if this cycle's summarization then fails."""
    monkeypatch.setattr("octopus_app.common.decorator.time.sleep", lambda seconds: None)
    mariadb_client.record_job_run("update_consumption_summary", "success")
    scheduler = Scheduler()
    consumption_summary = Mock(spec=ConsumptionSummaryRetriever)
    consumption_summary.refresh.side_effect = RuntimeError("MariaDB unavailable")
    pruner = Mock(spec=DataPruner)

    job = register_consumption_summary_job(
        scheduler, consumption_summary, pruner, mariadb_client
    )
    job.run().join()

    pruner.run.assert_not_called()
    with mariadb_client.session_read_scope() as session:
        prune_runs = (
            session.query(model.job_run).filter_by(job_name="prune_old_data").all()
        )
    assert len(prune_runs) == 1
    assert prune_runs[0].status == "skipped"


def test_backfill_runs_and_records_success_on_first_startup(
    mariadb_client: MariaDBClient,
) -> None:
    backfill = Mock(spec=ConsumptionSummaryBackfill)

    worker = run_backfill_at_startup(backfill, mariadb_client)
    assert worker is not None
    worker.join()

    backfill.run.assert_called_once()
    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).all()
    assert len(runs) == 1
    assert runs[0].job_name == "yearly_comparison_backfill"
    assert runs[0].status == "success"


STARTUP = dt(2026, 10, 10, tzinfo=datetime.UTC)


def _summary_day(day: datetime.date, count: int | None) -> ConsumptionSummary:
    return ConsumptionSummary(
        energy=Energy.gas, date=day, total_kwh=Decimal(1), half_hour_count=count
    )


def test_backfill_runs_when_the_summary_holds_less_than_six_months_of_counted_days(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_consumption_summary(
        [_summary_day(datetime.date(2026, 5, 1), 48)]
    )
    backfill = Mock(spec=ConsumptionSummaryBackfill)

    worker = run_backfill_at_startup(backfill, mariadb_client, as_of=STARTUP)
    assert worker is not None
    worker.join()

    backfill.run.assert_called_once()
    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).all()
    assert [(run.job_name, run.status) for run in runs] == [
        ("yearly_comparison_backfill", "success")
    ]


def test_backfill_is_skipped_once_the_summary_holds_six_months_of_counted_days(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_consumption_summary(
        [_summary_day(datetime.date(2026, 4, 1), 48)]
    )
    backfill = Mock(spec=ConsumptionSummaryBackfill)

    worker = run_backfill_at_startup(backfill, mariadb_client, as_of=STARTUP)

    assert worker is None
    backfill.run.assert_not_called()


def test_backfill_runs_when_the_summary_has_totals_but_no_counted_days(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_consumption_summary(
        [_summary_day(datetime.date(2024, 10, 1), None)]
    )
    backfill = Mock(spec=ConsumptionSummaryBackfill)

    worker = run_backfill_at_startup(backfill, mariadb_client, as_of=STARTUP)
    assert worker is not None
    worker.join()

    backfill.run.assert_called_once()


def test_a_persistently_failing_backfill_retries_with_backoff_and_does_not_crash(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleep_delays: list[int] = []
    monkeypatch.setattr("octopus_app.common.decorator.time.sleep", sleep_delays.append)
    backfill = Mock(spec=ConsumptionSummaryBackfill)
    backfill.run.side_effect = RuntimeError("Octopus API unavailable")

    worker = run_backfill_at_startup(backfill, mariadb_client)
    assert worker is not None
    worker.join()

    assert sleep_delays == [60, 120, 240, 480]
    assert backfill.run.call_count == 5

    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).all()
    assert len(runs) == 5
    assert all(run.status == "failure" for run in runs)


def test_consumption_backfill_job_is_registered_daily_at_0400(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()

    job = register_consumption_backfill_job(
        scheduler, REFRESH_CONFIG, Mock(spec=ConsumptionRetriever), mariadb_client
    )

    assert job.unit == "days"
    assert str(job.at_time) == "04:00:00"


def test_a_successful_backfill_run_calls_retrieve_and_is_recorded_as_successful(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()
    consumption = Mock(spec=ConsumptionRetriever)

    job = register_consumption_backfill_job(
        scheduler, REFRESH_CONFIG, consumption, mariadb_client
    )
    job.run().join()

    consumption.retrieve.assert_called_once()
    consumption.refresh.assert_not_called()

    with mariadb_client.session_read_scope() as session:
        runs = (
            session.query(model.job_run)
            .filter_by(job_name="consumption_backfill")
            .all()
        )

    assert len(runs) == 1
    assert runs[0].status == "success"


def test_a_backfill_run_recomputes_period_from_at_call_time_not_registration(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduler = Scheduler()
    consumption = Mock(spec=ConsumptionRetriever)
    current_time = dt(2026, 1, 1, tzinfo=datetime.UTC)

    class FrozenDatetime(dt):
        @classmethod
        def now(cls, tz: datetime.tzinfo | None = None) -> dt:  # type: ignore[override]
            return current_time

    monkeypatch.setattr("octopus_app.main.dt", FrozenDatetime)

    job = register_consumption_backfill_job(
        scheduler, REFRESH_CONFIG, consumption, mariadb_client
    )

    current_time = dt(2026, 6, 1, tzinfo=datetime.UTC)
    job.run().join()

    _, kwargs = consumption.retrieve.call_args
    expected = (current_time - timedelta(days=REFRESH_CONFIG.retention)).date()
    assert kwargs["period_from"].date() == expected


def test_a_persistently_failing_backfill_retries_with_exponential_backoff(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleep_delays: list[int] = []
    monkeypatch.setattr("octopus_app.common.decorator.time.sleep", sleep_delays.append)
    scheduler = Scheduler()
    consumption = Mock(spec=ConsumptionRetriever)
    consumption.retrieve.side_effect = RuntimeError("Octopus API unavailable")

    job = register_consumption_backfill_job(
        scheduler, REFRESH_CONFIG, consumption, mariadb_client
    )
    job.run().join()

    assert sleep_delays == [60, 120, 240, 480]
    assert consumption.retrieve.call_count == 5

    with mariadb_client.session_read_scope() as session:
        runs = (
            session.query(model.job_run)
            .filter_by(job_name="consumption_backfill")
            .all()
        )

    assert len(runs) == 5
    assert all(run.status == "failure" for run in runs)
    assert all(run.error_message == "Octopus API unavailable" for run in runs)
