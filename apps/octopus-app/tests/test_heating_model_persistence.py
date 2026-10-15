from datetime import UTC, date, datetime

from octopus_app.data.model import HeatingModelRecord
from octopus_app.data.mysql.client import MariaDBClient


def _record(
    fitted_at: datetime, threshold: float | None = 13.0, usable: bool = True
) -> HeatingModelRecord:
    return HeatingModelRecord(
        fitted_at=fitted_at,
        window_start=date(2025, 10, 13),
        window_end=date(2026, 10, 12),
        usable=usable,
        unusable_reason=None if usable else "the fit explains too little",
        days_used=333,
        threshold=threshold,
        upper_threshold=14.0,
        slope=3.76,
        weight=0.8,
        r_squared=0.69,
        baseload=8.17,
        baseload_measured_days=300,
        baseload_interpolated_days=60,
        baseload_held_days=5,
        noise_base=3.42,
        noise_per_degree_day=1.25,
        noise_floor=1.0,
        thermostat_threshold=None,
        heating_days=None,
        non_heating_days=None,
        label="estimated",
    )


def test_the_latest_heating_model_is_read_back_with_its_id(
    mariadb_client: MariaDBClient,
) -> None:
    older = _record(datetime(2026, 10, 1, 4, 0, tzinfo=UTC), threshold=12.0)
    newer = _record(datetime(2026, 10, 8, 4, 0, tzinfo=UTC), threshold=13.0)
    mariadb_client.write_heating_model(older)
    newer_id = mariadb_client.write_heating_model(newer)

    latest = mariadb_client.read_latest_heating_model()

    assert latest is not None
    assert latest.id == newer_id
    assert latest.model == newer


def test_the_newest_fit_wins_regardless_of_the_order_it_was_written(
    mariadb_client: MariaDBClient,
) -> None:
    newer = _record(datetime(2026, 10, 8, 4, 0, tzinfo=UTC), threshold=13.0)
    older = _record(datetime(2026, 10, 1, 4, 0, tzinfo=UTC), threshold=12.0)
    mariadb_client.write_heating_model(newer)
    mariadb_client.write_heating_model(older)

    latest = mariadb_client.read_latest_heating_model()

    assert latest is not None
    assert latest.model == newer


def test_two_fits_at_the_same_instant_resolve_to_the_later_written(
    mariadb_client: MariaDBClient,
) -> None:
    instant = datetime(2026, 10, 8, 4, 0, tzinfo=UTC)
    mariadb_client.write_heating_model(_record(instant, threshold=12.0))
    second_id = mariadb_client.write_heating_model(_record(instant, threshold=13.0))

    latest = mariadb_client.read_latest_heating_model()

    assert latest is not None
    assert latest.id == second_id


def test_a_newer_unusable_fit_is_the_latest_but_not_the_latest_usable(
    mariadb_client: MariaDBClient,
) -> None:
    usable = _record(datetime(2026, 10, 1, 4, 0, tzinfo=UTC))
    unusable = _record(datetime(2026, 10, 8, 4, 0, tzinfo=UTC), usable=False)
    usable_id = mariadb_client.write_heating_model(usable)
    unusable_id = mariadb_client.write_heating_model(unusable)

    latest = mariadb_client.read_latest_heating_model()
    latest_usable = mariadb_client.read_latest_usable_heating_model()

    assert latest is not None
    assert latest.id == unusable_id
    assert latest_usable is not None
    assert latest_usable.id == usable_id
    assert latest_usable.model == usable


def test_no_heating_model_has_been_fitted_yet(
    mariadb_client: MariaDBClient,
) -> None:
    assert mariadb_client.read_latest_heating_model() is None
    assert mariadb_client.read_latest_usable_heating_model() is None


def test_an_unusable_fit_with_no_numbers_round_trips(
    mariadb_client: MariaDBClient,
) -> None:
    empty = HeatingModelRecord(
        fitted_at=datetime(2026, 10, 8, 4, 0, tzinfo=UTC),
        window_start=date(2025, 10, 13),
        window_end=date(2026, 10, 12),
        usable=False,
        unusable_reason="too few days",
        days_used=None,
        threshold=None,
        upper_threshold=None,
        slope=None,
        weight=None,
        r_squared=None,
        baseload=None,
        baseload_measured_days=None,
        baseload_interpolated_days=None,
        baseload_held_days=None,
        noise_base=None,
        noise_per_degree_day=None,
        noise_floor=None,
        thermostat_threshold=None,
        heating_days=None,
        non_heating_days=None,
        label="estimated",
    )
    mariadb_client.write_heating_model(empty)

    latest = mariadb_client.read_latest_heating_model()

    assert latest is not None
    assert latest.model == empty
