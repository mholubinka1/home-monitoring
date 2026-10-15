from datetime import UTC, date, datetime

from octopus_app.data.model import HeatingForecastRecord
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient

COMPUTED_AT = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)


def _day(day: date, expected_gas: float = 25.0) -> HeatingForecastRecord:
    return HeatingForecastRecord(
        date=day,
        forecast_mean=6.5,
        effective_temperature=7.0,
        expected_gas=expected_gas,
    )


def test_forecast_days_are_read_back_in_date_order_with_their_model(
    mariadb_client: MariaDBClient,
) -> None:
    first = _day(date(2026, 10, 16))
    second = _day(date(2026, 10, 17), expected_gas=30.0)
    mariadb_client.replace_heating_forecast_days(3, [second, first], COMPUTED_AT)

    stored = mariadb_client.read_heating_forecast_days()

    assert stored == [(3, first), (3, second)]


def test_the_forecast_remembers_when_it_was_computed(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.replace_heating_forecast_days(
        3, [_day(date(2026, 10, 16))], COMPUTED_AT
    )

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.heating_forecast_day).one()

    assert stored.computed_at == COMPUTED_AT.replace(tzinfo=None)


def test_replacing_the_forecast_drops_days_no_longer_projected(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.replace_heating_forecast_days(
        1, [_day(date(2026, 10, 16)), _day(date(2026, 10, 17))], COMPUTED_AT
    )

    mariadb_client.replace_heating_forecast_days(
        2, [_day(date(2026, 10, 17), expected_gas=31.0)], COMPUTED_AT
    )

    assert mariadb_client.read_heating_forecast_days() == [
        (2, _day(date(2026, 10, 17), expected_gas=31.0))
    ]
