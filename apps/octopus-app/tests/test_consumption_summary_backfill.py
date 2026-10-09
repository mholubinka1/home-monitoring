from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import responses

from octopus_app.common.config import OctopusAPISettings
from octopus_app.data.consumption_summary import ConsumptionSummaryBackfill
from octopus_app.data.gas_day import GasDayStatus, gas_day_status
from octopus_app.data.local_day import expected_half_hour_count
from octopus_app.data.model import Consumption, ConsumptionSummary, Energy
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.octopus.api import OctopusEnergyAPIClient
from octopus_app.data.octopus.model import Agreement, Electricity, Meter

CONSUMPTION_ENDPOINT = (
    "https://api.octopus.energy/v1/electricity-meter-points/"
    "1234567890123/meters/00A1234567/consumption/"
)


class _RealConsumptionSummaryBackfillSource:
    """A real ConsumptionSummaryBackfillSource adapter for tests: genuine
    OctopusEnergyAPIClient and MariaDBClient underneath, with meters fixed up
    front rather than fetched, so tests only need to mock the consumption
    HTTP endpoints ConsumptionSummaryBackfill actually calls."""

    def __init__(
        self,
        octopus: OctopusEnergyAPIClient,
        mariadb: MariaDBClient,
        meters: list[Meter],
    ) -> None:
        self._octopus = octopus
        self._mariadb = mariadb
        self.meters = meters

    def refresh_meters(self) -> None:
        pass

    def fetch_consumption(
        self, meter: Meter, period_from: datetime
    ) -> tuple[str | None, list[Consumption]]:
        return self._octopus.get_consumption(meter, period_from)

    def fetch_consumption_page(
        self, energy: Energy, next_page: str
    ) -> tuple[str | None, list[Consumption]]:
        return self._octopus.get_consumption_directly_from_endpoint(energy, next_page)

    def persist_consumption_summary(self, summaries: list[ConsumptionSummary]) -> None:
        self._mariadb.write_consumption_summary(summaries)


def _make_meter() -> Electricity:
    return Electricity(
        mpan="1234567890123",
        serial_number="00A1234567",
        agreements=[
            Agreement(
                tariff_code="E-1R-VAR-22-11-01-A",
                valid_from=datetime(2022, 11, 1, tzinfo=UTC),
                valid_to=None,
            )
        ],
    )


@responses.activate
def test_run_summarizes_the_fetched_consumption_history_without_writing_raw_rows(
    mariadb_client: MariaDBClient,
) -> None:
    as_of = datetime(2026, 1, 15, tzinfo=UTC)
    period_from = as_of - timedelta(days=1096)

    responses.add(
        responses.GET,
        CONSUMPTION_ENDPOINT
        + f"?page_size=5000&period_from={period_from.isoformat().replace('+00:00', 'Z')}"
        "&order_by=-period",
        json={
            "results": [
                {
                    "consumption": "1.5",
                    "interval_start": "2024-06-01T00:00:00+00:00",
                    "interval_end": "2024-06-01T00:30:00+00:00",
                },
                {
                    "consumption": "2.5",
                    "interval_start": "2024-06-01T00:30:00+00:00",
                    "interval_end": "2024-06-01T01:00:00+00:00",
                },
                {
                    "consumption": "1.0",
                    "interval_start": "2024-06-02T00:00:00+00:00",
                    "interval_end": "2024-06-02T00:30:00+00:00",
                },
            ],
            "next": None,
        },
        status=200,
    )

    meter = _make_meter()
    octopus = OctopusEnergyAPIClient(
        OctopusAPISettings(account_number="A-1234ABCD", api_key="sk_live_test")
    )
    source = _RealConsumptionSummaryBackfillSource(octopus, mariadb_client, [meter])
    backfill = ConsumptionSummaryBackfill(source)

    backfill.run(as_of=as_of)

    with mariadb_client.session_read_scope() as session:
        summary_rows = session.query(model.daily_consumption_summary).all()
        raw_rows = session.query(model.consumption).all()

    stored = {(row.energy, row.date): row.total_kwh for row in summary_rows}
    assert stored[("E", as_of.replace(year=2024, month=6, day=1).date())] == Decimal(
        "4.00000"
    )
    assert stored[("E", as_of.replace(year=2024, month=6, day=2).date())] == Decimal(
        "1.00000"
    )
    assert raw_rows == []


@responses.activate
def test_run_records_how_many_half_hours_back_each_backfilled_day(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        CONSUMPTION_ENDPOINT,
        json={
            "results": [
                {
                    "consumption": "1.5",
                    "interval_start": "2025-06-01T00:00:00+00:00",
                    "interval_end": "2025-06-01T00:30:00+00:00",
                },
                {
                    "consumption": "2.5",
                    "interval_start": "2025-06-01T00:30:00+00:00",
                    "interval_end": "2025-06-01T01:00:00+00:00",
                },
                {
                    "consumption": "1.0",
                    "interval_start": "2025-06-02T00:00:00+00:00",
                    "interval_end": "2025-06-02T00:30:00+00:00",
                },
            ],
            "next": None,
        },
        status=200,
    )
    octopus = OctopusEnergyAPIClient(
        OctopusAPISettings(account_number="A-1234ABCD", api_key="sk_live_test")
    )
    source = _RealConsumptionSummaryBackfillSource(
        octopus, mariadb_client, [_make_meter()]
    )

    ConsumptionSummaryBackfill(source).run(as_of=datetime(2026, 1, 15, tzinfo=UTC))

    counts = {
        summary.date: summary.half_hour_count
        for summary in mariadb_client.read_daily_consumption_summary(
            Energy.electricity, date(2025, 6, 1), date(2025, 6, 2)
        )
    }
    assert counts == {date(2025, 6, 1): 2, date(2025, 6, 2): 1}


@responses.activate
def test_run_leaves_a_day_before_the_apis_first_day_uncounted_and_its_total_untouched(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_consumption_summary(
        [
            ConsumptionSummary(
                energy=Energy.electricity,
                date=date(2023, 3, 1),
                total_kwh=Decimal("9.5"),
            )
        ]
    )
    responses.add(
        responses.GET, CONSUMPTION_ENDPOINT, json={"results": [], "next": None}
    )
    source = _RealConsumptionSummaryBackfillSource(
        OctopusEnergyAPIClient(
            OctopusAPISettings(account_number="A-1234ABCD", api_key="sk_live_test")
        ),
        mariadb_client,
        [_make_meter()],
    )

    ConsumptionSummaryBackfill(source).run(as_of=datetime(2026, 1, 15, tzinfo=UTC))

    [summary] = mariadb_client.read_daily_consumption_summary(
        Energy.electricity, date(2023, 3, 1), date(2023, 3, 1)
    )
    assert summary.half_hour_count is None
    assert summary.total_kwh == Decimal("9.5")


@responses.activate
def test_run_marks_a_partial_api_day_incomplete(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        CONSUMPTION_ENDPOINT,
        json={
            "results": [
                {
                    "consumption": "1.5",
                    "interval_start": "2025-06-01T00:00:00+01:00",
                    "interval_end": "2025-06-01T00:30:00+01:00",
                }
            ],
            "next": None,
        },
    )
    source = _RealConsumptionSummaryBackfillSource(
        OctopusEnergyAPIClient(
            OctopusAPISettings(account_number="A-1234ABCD", api_key="sk_live_test")
        ),
        mariadb_client,
        [_make_meter()],
    )

    ConsumptionSummaryBackfill(source).run(as_of=datetime(2026, 1, 15, tzinfo=UTC))

    [summary] = mariadb_client.read_daily_consumption_summary(
        Energy.electricity, date(2025, 6, 1), date(2025, 6, 1)
    )
    assert summary.half_hour_count is not None
    assert summary.half_hour_count < expected_half_hour_count(summary.date)
    assert gas_day_status(summary) is GasDayStatus.INCOMPLETE


@responses.activate
def test_run_twice_leaves_the_counts_unchanged(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        CONSUMPTION_ENDPOINT,
        json={
            "results": [
                {
                    "consumption": "1.5",
                    "interval_start": "2025-06-01T00:00:00+00:00",
                    "interval_end": "2025-06-01T00:30:00+00:00",
                },
                {
                    "consumption": "2.5",
                    "interval_start": "2025-06-01T00:30:00+00:00",
                    "interval_end": "2025-06-01T01:00:00+00:00",
                },
            ],
            "next": None,
        },
    )
    backfill = ConsumptionSummaryBackfill(
        _RealConsumptionSummaryBackfillSource(
            OctopusEnergyAPIClient(
                OctopusAPISettings(account_number="A-1234ABCD", api_key="sk_live_test")
            ),
            mariadb_client,
            [_make_meter()],
        )
    )

    backfill.run(as_of=datetime(2026, 1, 15, tzinfo=UTC))
    backfill.run(as_of=datetime(2026, 1, 15, tzinfo=UTC))

    [summary] = mariadb_client.read_daily_consumption_summary(
        Energy.electricity, date(2025, 6, 1), date(2025, 6, 1)
    )
    assert summary.half_hour_count == 2


@responses.activate
def test_run_anchors_period_from_to_midnight_even_when_as_of_has_a_time_component(
    mariadb_client: MariaDBClient,
) -> None:
    # A non-midnight as_of (e.g. the app started mid-day) must not leak its
    # time-of-day into period_from -- Octopus would then omit intervals
    # before that time on the oldest backfilled day, producing a partial
    # daily total for it.
    as_of = datetime(2026, 1, 15, 14, 32, 7, tzinfo=UTC)
    expected_period_from = datetime(2026, 1, 15, tzinfo=UTC) - timedelta(days=1096)

    responses.add(
        responses.GET,
        CONSUMPTION_ENDPOINT
        + "?page_size=5000&period_from="
        + expected_period_from.isoformat().replace("+00:00", "Z")
        + "&order_by=-period",
        json={"results": [], "next": None},
        status=200,
    )

    meter = _make_meter()
    octopus = OctopusEnergyAPIClient(
        OctopusAPISettings(account_number="A-1234ABCD", api_key="sk_live_test")
    )
    source = _RealConsumptionSummaryBackfillSource(octopus, mariadb_client, [meter])
    backfill = ConsumptionSummaryBackfill(source)

    # If period_from had carried as_of's 14:32:07 time-of-day instead of
    # being anchored to midnight, the registered response above wouldn't
    # match and `responses` would raise a ConnectionError instead.
    backfill.run(as_of=as_of)


@responses.activate
def test_run_buckets_each_interval_by_its_local_day_across_the_bst_boundary(
    mariadb_client: MariaDBClient,
) -> None:
    # Octopus returns local-offset timestamps. On a BST day the first two
    # half-hours (00:00 and 00:30 +01:00) are 23:00 and 23:30 UTC the day
    # before, so bucketing by the UTC date would split Jul 10 across two rows
    # and disagree with the weekly job, which buckets by local day.
    as_of = datetime(2026, 7, 15, tzinfo=UTC)
    period_from = as_of - timedelta(days=1096)

    responses.add(
        responses.GET,
        CONSUMPTION_ENDPOINT
        + f"?page_size=5000&period_from={period_from.isoformat().replace('+00:00', 'Z')}"
        "&order_by=-period",
        json={
            "results": [
                {
                    "consumption": "1.0",
                    "interval_start": "2025-07-10T00:00:00+01:00",
                    "interval_end": "2025-07-10T00:30:00+01:00",
                },
                {
                    "consumption": "2.0",
                    "interval_start": "2025-07-10T00:30:00+01:00",
                    "interval_end": "2025-07-10T01:00:00+01:00",
                },
                {
                    "consumption": "4.0",
                    "interval_start": "2025-07-10T23:30:00+01:00",
                    "interval_end": "2025-07-11T00:00:00+01:00",
                },
            ],
            "next": None,
        },
        status=200,
    )

    octopus = OctopusEnergyAPIClient(
        OctopusAPISettings(account_number="A-1234ABCD", api_key="sk_live_test")
    )
    source = _RealConsumptionSummaryBackfillSource(
        octopus, mariadb_client, [_make_meter()]
    )

    ConsumptionSummaryBackfill(source).run(as_of=as_of)

    with mariadb_client.session_read_scope() as session:
        stored = {
            (row.energy, row.date): row.total_kwh
            for row in session.query(model.daily_consumption_summary).all()
        }
    assert stored == {("E", date(2025, 7, 10)): Decimal("7.00000")}
