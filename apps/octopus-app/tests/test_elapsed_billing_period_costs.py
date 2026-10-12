from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from octopus_app.data.model import Energy
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient

PRODUCT_CODE = "VAR-24-10-01"
REGION = "H"
LONDON = ZoneInfo("Europe/London")


def _seed_agreement(
    s: Session, valid_from: datetime, valid_to: datetime | None = None
) -> None:
    s.add(
        model.agreement(
            id=f"E{valid_from.strftime('%Y%m%d%H%M%S')}",
            energy="E",
            product_code=PRODUCT_CODE,
            tariff_code=f"E-1R-{PRODUCT_CODE}-{REGION}",
            valid_from=valid_from,
            valid_to=valid_to,
        )
    )


def _seed_rate(
    s: Session,
    valid_from: datetime,
    valid_to: datetime | None,
    unit_rate: str,
    standing_charge: str,
) -> None:
    s.add(
        model.product_rate(
            id=f"{PRODUCT_CODE}_{REGION}_{valid_from.strftime('%Y%m%d%H%M')}",
            product_code=PRODUCT_CODE,
            region=REGION,
            valid_from=valid_from,
            valid_to=valid_to,
            unit_rate=Decimal(unit_rate),
            standing_charge=Decimal(standing_charge),
        )
    )


def _seed_consumption(s: Session, period_from: datetime, est_kwh: str) -> None:
    s.add(
        model.consumption(
            id=f"E{period_from.strftime('%Y%m%d%H%M%S')}",
            energy="E",
            period_from=period_from,
            period_to=period_from,
            raw_value=Decimal(est_kwh),
            unit="kWh",
            est_kwh=Decimal(est_kwh),
        )
    )


def _local_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=LONDON).astimezone(UTC)


def _seed_complete_day(s: Session, day: date, est_kwh_per_slot: str = "0.1") -> None:
    start = _local_midnight(day)
    for slot in range(48):
        _seed_consumption(s, start + timedelta(minutes=30 * slot), est_kwh_per_slot)


def test_a_full_local_day_spanning_a_utc_midnight_boundary_is_grouped_as_one_day(
    mariadb_client: MariaDBClient,
) -> None:
    # Local midnight on 2026-07-06 (BST) is UTC 2026-07-05 23:00 -- a
    # genuine complete local day's half-hourly slots straddle the UTC
    # calendar boundary, but must still group under a single local date,
    # not split 2/46 across the two UTC dates.
    with mariadb_client.session_write_scope() as s:
        _seed_agreement(s, datetime(2026, 1, 1, tzinfo=UTC))
        _seed_rate(s, datetime(2026, 1, 1, tzinfo=UTC), None, "20.00", "48.00")
        _seed_complete_day(s, date(2026, 7, 6), "0.1")

    results = mariadb_client.read_elapsed_billing_period_costs(
        _local_midnight(date(2026, 7, 5)),
        _local_midnight(date(2026, 7, 8)),
        REGION,
        Energy.electricity,
    )

    by_date = {r.date: r for r in results}
    assert date(2026, 7, 5) not in by_date
    assert by_date[date(2026, 7, 6)].total_kwh == Decimal("4.8")
    # (4.8 kWh @ 20.00p) + 48.00p standing charge = 144.00p -> /100 = 1.44 GBP
    assert by_date[date(2026, 7, 6)].day_cost_gbp == Decimal("1.44")


def test_the_uk_spring_forward_date_with_forty_six_rows_is_treated_as_complete(
    mariadb_client: MariaDBClient,
) -> None:
    # 2026-03-29 is the UK spring-forward date -- the local day is only 23
    # hours long, so a genuinely complete day has 46 half-hourly rows, not
    # 48. Treating it as incomplete would wrongly exclude a real day.
    with mariadb_client.session_write_scope() as s:
        _seed_agreement(s, datetime(2026, 1, 1, tzinfo=UTC))
        _seed_rate(s, datetime(2026, 1, 1, tzinfo=UTC), None, "20.00", "48.00")
        start = _local_midnight(date(2026, 3, 29))
        for slot in range(46):
            _seed_consumption(s, start + timedelta(minutes=30 * slot), "0.1")

    results = mariadb_client.read_elapsed_billing_period_costs(
        _local_midnight(date(2026, 3, 29)),
        _local_midnight(date(2026, 3, 31)),
        REGION,
        Energy.electricity,
    )

    by_date = {r.date: r for r in results}
    assert by_date[date(2026, 3, 29)].total_kwh == Decimal("4.6")


def test_the_uk_fall_back_date_with_fifty_rows_is_treated_as_complete(
    mariadb_client: MariaDBClient,
) -> None:
    # 2026-10-25 is the UK fall-back date -- the local day is 25 hours
    # long, so a genuinely complete day has 50 half-hourly rows.
    with mariadb_client.session_write_scope() as s:
        _seed_agreement(s, datetime(2026, 1, 1, tzinfo=UTC))
        _seed_rate(s, datetime(2026, 1, 1, tzinfo=UTC), None, "20.00", "48.00")
        start = _local_midnight(date(2026, 10, 25))
        for slot in range(50):
            _seed_consumption(s, start + timedelta(minutes=30 * slot), "0.1")

    results = mariadb_client.read_elapsed_billing_period_costs(
        _local_midnight(date(2026, 10, 25)),
        _local_midnight(date(2026, 10, 27)),
        REGION,
        Energy.electricity,
    )

    by_date = {r.date: r for r in results}
    assert by_date[date(2026, 10, 25)].total_kwh == Decimal("5.0")


def test_days_either_side_of_a_clock_change_still_require_forty_eight_rows(
    mariadb_client: MariaDBClient,
) -> None:
    with mariadb_client.session_write_scope() as s:
        _seed_agreement(s, datetime(2026, 1, 1, tzinfo=UTC))
        _seed_rate(s, datetime(2026, 1, 1, tzinfo=UTC), None, "20.00", "48.00")
        # Only 46 rows on an ordinary (non-clock-change) day either side of
        # the spring-forward date -- still incomplete, unlike the
        # clock-change date itself.
        start = _local_midnight(date(2026, 3, 28))
        for slot in range(46):
            _seed_consumption(s, start + timedelta(minutes=30 * slot), "0.1")

    results = mariadb_client.read_elapsed_billing_period_costs(
        _local_midnight(date(2026, 3, 28)),
        _local_midnight(date(2026, 3, 30)),
        REGION,
        Energy.electricity,
    )

    assert results == []


def test_an_incomplete_past_day_is_excluded_from_the_result(
    mariadb_client: MariaDBClient,
) -> None:
    # Octopus's settlement lag means a day can still be missing rows more
    # than 24 hours after it ended -- only 30 of the day's 48 half-hourly
    # slots have arrived so far.
    with mariadb_client.session_write_scope() as s:
        _seed_agreement(s, datetime(2026, 1, 1, tzinfo=UTC))
        _seed_rate(s, datetime(2026, 1, 1, tzinfo=UTC), None, "20.00", "48.00")
        start = datetime(2026, 7, 6, tzinfo=UTC)
        for slot in range(30):
            _seed_consumption(s, start + timedelta(minutes=30 * slot), "1.0")

    results = mariadb_client.read_elapsed_billing_period_costs(
        datetime(2026, 7, 6, tzinfo=UTC),
        datetime(2026, 7, 7, tzinfo=UTC),
        REGION,
        Energy.electricity,
    )

    assert results == []


def test_the_current_in_progress_day_is_included_regardless_of_row_count(
    mariadb_client: MariaDBClient,
) -> None:
    # period_to's date is the current/most-recent day -- it's expected to
    # be partial by definition ("cost so far"), so it's exempt from the
    # completeness guard that applies to strictly-past days.
    with mariadb_client.session_write_scope() as s:
        _seed_agreement(s, datetime(2026, 1, 1, tzinfo=UTC))
        _seed_rate(s, datetime(2026, 1, 1, tzinfo=UTC), None, "20.00", "48.00")
        _seed_consumption(s, datetime(2026, 7, 6, 0, 0, tzinfo=UTC), "1.0")

    results = mariadb_client.read_elapsed_billing_period_costs(
        datetime(2026, 7, 6, tzinfo=UTC),
        datetime(2026, 7, 6, 12, 0, tzinfo=UTC),
        REGION,
        Energy.electricity,
    )

    by_date = {r.date: r for r in results}
    assert by_date[date(2026, 7, 6)].total_kwh == Decimal("1.0")
    # (1.0 kWh @ 20.00p) + 48.00p standing charge = 68.00p -> /100 = 0.68 GBP
    assert by_date[date(2026, 7, 6)].day_cost_gbp == Decimal("0.68")


def test_two_elapsed_days_with_consumption_on_a_stable_rate(
    mariadb_client: MariaDBClient,
) -> None:
    with mariadb_client.session_write_scope() as s:
        _seed_agreement(s, datetime(2026, 1, 1, tzinfo=UTC))
        _seed_rate(
            s,
            datetime(2026, 1, 1, tzinfo=UTC),
            None,
            "20.00",
            "48.00",
        )
        # Full 48-slot days -- both are strictly before period_to's local
        # date (2026-07-08), so both must be complete to count.
        _seed_complete_day(s, date(2026, 7, 6), "0.1")
        _seed_complete_day(s, date(2026, 7, 7), "0.1")

    results = mariadb_client.read_elapsed_billing_period_costs(
        _local_midnight(date(2026, 7, 6)),
        _local_midnight(date(2026, 7, 8)),
        REGION,
        Energy.electricity,
    )

    by_date = {r.date: r for r in results}
    assert by_date[date(2026, 7, 6)].total_kwh == Decimal("4.8")
    # (4.8 kWh @ 20.00p) + 48.00p standing charge = 144.00p -> /100 = 1.44 GBP
    assert by_date[date(2026, 7, 6)].day_cost_gbp == Decimal("1.44")
    assert by_date[date(2026, 7, 7)].total_kwh == Decimal("4.8")
    assert by_date[date(2026, 7, 7)].day_cost_gbp == Decimal("1.44")


def test_a_rate_for_another_region_is_not_double_matched(
    mariadb_client: MariaDBClient,
) -> None:
    # Regression test: the join to product_rate must be scoped by region,
    # not just product_code + validity window. Octopus tariffs price the
    # same product_code differently per GSP region, so product_rate holds
    # one row per (product_code, region, valid_from) -- an unscoped join
    # would multiply-match every region sharing that product_code/window,
    # overcounting both kWh and cost.
    with mariadb_client.session_write_scope() as s:
        _seed_agreement(s, datetime(2026, 1, 1, tzinfo=UTC))
        _seed_rate(
            s,
            datetime(2026, 1, 1, tzinfo=UTC),
            None,
            "20.00",
            "48.00",
        )
        # Same product_code, same validity window, a *different* region --
        # must never be joined against this account's (region H) consumption.
        s.add(
            model.product_rate(
                id=f"{PRODUCT_CODE}_A_202601010000",
                product_code=PRODUCT_CODE,
                region="A",
                valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                valid_to=None,
                unit_rate=Decimal("99.00"),
                standing_charge=Decimal("99.00"),
            )
        )
        # Full 48-slot day -- strictly before period_to's local date
        # (2026-07-07).
        _seed_complete_day(s, date(2026, 7, 6), "0.1")

    results = mariadb_client.read_elapsed_billing_period_costs(
        _local_midnight(date(2026, 7, 6)),
        _local_midnight(date(2026, 7, 7)),
        REGION,
        Energy.electricity,
    )

    by_date = {r.date: r for r in results}
    assert by_date[date(2026, 7, 6)].total_kwh == Decimal("4.8")
    # If the region-A row were incorrectly matched too, total_kwh would be
    # doubled (9.6) and day_cost_gbp would include the 99.00p rate as well.
    assert by_date[date(2026, 7, 6)].day_cost_gbp == Decimal("1.44")


def test_a_mid_period_rate_change_is_reflected_per_half_hour_not_flattened(
    mariadb_client: MariaDBClient,
) -> None:
    local_noon = datetime(2026, 7, 6, 12, 0, tzinfo=LONDON).astimezone(UTC)
    with mariadb_client.session_write_scope() as s:
        _seed_agreement(s, datetime(2026, 1, 1, tzinfo=UTC))
        # Old rate covers the first half of the local day; a new rate takes
        # over at local noon -- both apply to consumption on the same local
        # calendar day.
        _seed_rate(
            s,
            datetime(2026, 1, 1, tzinfo=UTC),
            local_noon,
            "20.00",
            "48.00",
        )
        _seed_rate(
            s,
            local_noon,
            None,
            "30.00",
            "48.00",
        )
        # Full 48-slot local day, split across the noon rate change: 24
        # slots at the old rate, 24 at the new one.
        morning_start = _local_midnight(date(2026, 7, 6))
        for slot in range(24):
            _seed_consumption(s, morning_start + timedelta(minutes=30 * slot), "0.1")
        for slot in range(24):
            _seed_consumption(s, local_noon + timedelta(minutes=30 * slot), "0.1")

    results = mariadb_client.read_elapsed_billing_period_costs(
        _local_midnight(date(2026, 7, 6)),
        _local_midnight(date(2026, 7, 7)),
        REGION,
        Energy.electricity,
    )

    by_date = {r.date: r for r in results}
    # (2.4 kWh @ 20.00p) + (2.4 kWh @ 30.00p) + 48.00p standing = 168.00p
    assert by_date[date(2026, 7, 6)].day_cost_gbp == Decimal("1.68")


def test_a_mid_day_standing_charge_change_uses_the_higher_of_the_two(
    mariadb_client: MariaDBClient,
) -> None:
    # A rate change mid-day can carry a different standing charge too --
    # the day's cost must deterministically pick the higher one (matching
    # this file's prior MAX-based behaviour), not whichever row a DB engine
    # happens to return last from an unordered result set. The higher
    # charge (55.00) is seeded on the *morning* rate and consumption rows,
    # inserted first -- an implementation that just overwrites with the
    # last-seen row (rather than taking a max) would wrongly end up with
    # the lower afternoon charge (40.00) instead.
    local_noon = datetime(2026, 7, 6, 12, 0, tzinfo=LONDON).astimezone(UTC)
    with mariadb_client.session_write_scope() as s:
        _seed_agreement(s, datetime(2026, 1, 1, tzinfo=UTC))
        _seed_rate(s, datetime(2026, 1, 1, tzinfo=UTC), local_noon, "20.00", "55.00")
        _seed_rate(s, local_noon, None, "20.00", "40.00")
        _seed_complete_day(s, date(2026, 7, 6), "0.1")

    results = mariadb_client.read_elapsed_billing_period_costs(
        _local_midnight(date(2026, 7, 6)),
        _local_midnight(date(2026, 7, 7)),
        REGION,
        Energy.electricity,
    )

    by_date = {r.date: r for r in results}
    # (4.8 kWh @ 20.00p) + 55.00p (the higher standing charge) = 151.00p
    assert by_date[date(2026, 7, 6)].day_cost_gbp == Decimal("1.51")


def test_a_mid_day_standing_charge_change_uses_the_higher_regardless_of_row_order(
    mariadb_client: MariaDBClient,
) -> None:
    # Complements the test above with the seeding order reversed (higher
    # charge on the *afternoon* rate, inserted second) -- the result must
    # be the same higher charge either way, proving the selection is
    # order-independent rather than incidentally correct for one
    # particular row-return order.
    local_noon = datetime(2026, 7, 6, 12, 0, tzinfo=LONDON).astimezone(UTC)
    with mariadb_client.session_write_scope() as s:
        _seed_agreement(s, datetime(2026, 1, 1, tzinfo=UTC))
        _seed_rate(s, datetime(2026, 1, 1, tzinfo=UTC), local_noon, "20.00", "40.00")
        _seed_rate(s, local_noon, None, "20.00", "55.00")
        _seed_complete_day(s, date(2026, 7, 6), "0.1")

    results = mariadb_client.read_elapsed_billing_period_costs(
        _local_midnight(date(2026, 7, 6)),
        _local_midnight(date(2026, 7, 7)),
        REGION,
        Energy.electricity,
    )

    by_date = {r.date: r for r in results}
    # (4.8 kWh @ 20.00p) + 55.00p (the higher standing charge) = 151.00p
    assert by_date[date(2026, 7, 6)].day_cost_gbp == Decimal("1.51")
